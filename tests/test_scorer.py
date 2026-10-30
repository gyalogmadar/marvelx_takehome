"""Clef as the scorer: probabilities on each document reading and extracted fact, shown to the
judge, carried into the confidences, and never changing a conclusion.  The server is a mock transport."""
import json
from pathlib import Path

import httpx
import pytest

from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.domain import Outcome
from claims_pipeline.llm.anthropic_backend import render_readings, render_facts
from claims_pipeline.llm.clef_backend import ALTERED_OPTIONS, FACT_OPTIONS, ClefClient, ClefScorer
from claims_pipeline.llm.clients import Routed, with_routes
from claims_pipeline.llm.config import Settings
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


def settings(tmp_path, **kw) -> Settings:
    return Settings(clef_base_url="http://clef.local/v1", trace_dir=str(tmp_path / "trace"), **kw)


def clef_server(oracle: dict, seen: list, fact_p: float = 0.85):
    """Backs the oracle's document types at 0.9, sees no alteration at 0.8, and backs every fact at ``fact_p``."""
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        questions = body["questions"]
        if "doc_type" in questions:
            filename = body["state"].splitlines()[0].removeprefix("# Document: ")
            truth = oracle["documents"][filename]["doc_type"]
            types = questions["doc_type"]["criteria"]
            rest = 0.1 / (len(types) - 1)
            return httpx.Response(200, json={"answers": {
                "doc_type": {"choice": truth, "probabilities": {t: 0.9 if t == truth else rest for t in types}},
                "altered": {"choice": "no", "probabilities": {"yes": 0.1, "no": 0.8, "unknown": 0.1}}}})
        return httpx.Response(200, json={"answers": {
            name: {"choice": "yes", "probabilities": {"yes": fact_p, "no": (1 - fact_p) / 2, "unknown": (1 - fact_p) / 2}}
            for name in questions}})
    return handler


def scored_backend(tmp_path, oracle: dict, handler) -> Routed:
    http = httpx.Client(base_url="http://clef.local/v1", transport=httpx.MockTransport(handler))
    scorer = ClefScorer(settings(tmp_path), ClefClient(settings(tmp_path), http=http))
    return Routed(ScriptedBackend(oracle), score_document=scorer, score_facts=scorer)


def load(claim: str):
    loaded = load_claim_dir(ROOT / claim)
    return loaded, json.loads(loaded.oracle_path.read_text())


def test_documents_and_facts_carry_the_scores_and_the_decision_is_unchanged(tmp_path):
    loaded, oracle = load("01-clean-medical-cancellation")
    policy = get_policy(loaded.policy)
    plain = process_claim(loaded.input, policy, ScriptedBackend(oracle))
    seen: list = []
    result = process_claim(loaded.input, policy, scored_backend(tmp_path, oracle, clef_server(oracle, seen)))

    assert result.decision.outcome == plain.decision.outcome == Outcome.APPROVE
    assert result.decision.payout == plain.decision.payout
    files = [d for d in result.readings if d.kind not in ("description", "metadata")]
    assert files and all(d.scores["doc_type"][d.doc_type] == pytest.approx(0.9) for d in files)
    assert all(set(d.scores["altered"]) == set(ALTERED_OPTIONS) for d in files)
    scored = [f for f in result.facts if f.scores]
    assert scored and all(f.confidence == pytest.approx(0.85) and set(f.scores) == set(FACT_OPTIONS) for f in scored)
    submitted = next(f for f in result.facts if f.name == "claim_submitted")
    assert submitted.scores is None and submitted.confidence == 1.0       # the system's own date is not scored
    # one request per document, one for the facts, each logged under its own component
    assert [c.component for c in result.calls if c.model == "clef"] == ["score_document"] * len(files) + ["score_facts"]
    assert len(seen) == len(files) + 1


def test_the_scorer_sees_the_document_itself_never_the_transcription(tmp_path):
    loaded, oracle = load("08-altered-booking-image")
    seen: list = []
    process_claim(loaded.input, get_policy(loaded.policy), scored_backend(tmp_path, oracle, clef_server(oracle, seen)))
    image = next(b for b in seen if "doc_type" in b["questions"] and b.get("images"))
    assert image["images"][0].startswith("data:image/png;base64,")
    assert "the attached image" in image["state"] and "Total paid" not in image["state"]
    facts = next(b for b in seen if "doc_type" not in b["questions"])
    assert "# Facts already extracted" not in facts["state"]                  # each fact judged on the evidence alone


def test_the_judge_reads_the_scores(tmp_path):
    loaded, oracle = load("01-clean-medical-cancellation")
    result = process_claim(loaded.input, get_policy(loaded.policy),
                           scored_backend(tmp_path, oracle, clef_server(oracle, [])))
    docs, facts = render_readings(result.readings), render_facts(result.facts)
    assert "independent scorer: type medical_report 0.90" in docs and "altered no 0.80" in docs
    assert "independent scorer: yes 0.85" in facts


def test_fact_scores_carry_into_check_and_decision_confidence(tmp_path):
    loaded, oracle = load("01-clean-medical-cancellation")
    result = process_claim(loaded.input, get_policy(loaded.policy),
                           scored_backend(tmp_path, oracle, clef_server(oracle, [], fact_p=0.55)))
    period = next(e for e in result.exclusions if e.id.startswith("within_period"))
    assert period.confidence == pytest.approx(0.55)
    cancellation = next(c for c in result.coverages if c.name == "cancellation")
    assert cancellation.proofs.confidence == pytest.approx(0.9)               # weakest scored document type
    assert result.decision.outcome == Outcome.APPROVE and result.decision.confidence == pytest.approx(0.55)


def test_a_failed_scorer_leaves_the_components_own_confidences(tmp_path):
    loaded, oracle = load("01-clean-medical-cancellation")
    policy = get_policy(loaded.policy)
    plain = process_claim(loaded.input, policy, ScriptedBackend(oracle))
    result = process_claim(loaded.input, policy,
                           scored_backend(tmp_path, oracle, lambda r: httpx.Response(503, text="loading")))
    assert result.decision.outcome == plain.decision.outcome and result.decision.confidence == plain.decision.confidence
    assert [f.confidence for f in result.facts] == [f.confidence for f in plain.facts]
    assert all(not d.scores for d in result.readings)
    failed = [c for c in result.calls if not c.ok]
    assert failed and {c.component for c in failed} == {"score_document", "score_facts"}


def test_with_routes_adds_the_scorer_and_leaves_the_judge_alone(tmp_path):
    routed = with_routes(ScriptedBackend({}), settings(tmp_path, scorer="clef"))
    assert routed.name == "scripted+clef score_document/score_facts"
    assert "judge" not in routed.overrides and hasattr(routed, "score_facts")
    assert not hasattr(ScriptedBackend({}), "score_facts")
    with pytest.raises(Exception):
        with_routes(ScriptedBackend({}), Settings(scorer="oracle"))
