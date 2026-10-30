"""Clef, the decision model: reply parsing, Clef as the judge component on top of another
backend, and the end-to-end Clef strategy.  The server is a mock transport."""
import json
from pathlib import Path

import httpx
import pytest

from claims_pipeline.benchmark import run as bench
from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.clef_e2e import MAX_IMAGES, process_claim_clef
from claims_pipeline.domain import Outcome, Verdict
from claims_pipeline.llm import clef_backend
from claims_pipeline.llm.base import BackendError
from claims_pipeline.llm.clef_backend import JUDGE_OPTIONS, ClefClient, ClefJudge, read_choice
from claims_pipeline.llm.clients import Routed, with_routes
from claims_pipeline.llm.config import Settings
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import ClaimFile, process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


def settings(tmp_path, **kw) -> Settings:
    return Settings(clef_base_url="http://clef.local/v1", trace_dir=str(tmp_path / "trace"), **kw)


def clef_client(tmp_path, handler, **kw) -> ClefClient:
    http = httpx.Client(base_url="http://clef.local/v1", transport=httpx.MockTransport(handler))
    return ClefClient(settings(tmp_path, **kw), http=http)


def answer(choice: str, p: float, options) -> dict:
    rest = (1 - p) / (len(options) - 1)
    return {"choice": choice, "confidence": p, "probabilities": {k: (p if k == choice else rest) for k in options}}


def oracle_clef(oracle: dict, seen: list):
    """A Clef server that answers each judged check as the claim's oracle does, at p = 0.9."""
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        (name, question), = body["questions"].items()
        assert question["type"] == "choice" and set(question["criteria"]) == set(JUDGE_OPTIONS)
        return httpx.Response(200, json={"answers": {name: answer(oracle["judgments"][name]["answer"], 0.9, JUDGE_OPTIONS)}})
    return handler


# -- reply parsing ---------------------------------------------------------------------
def test_read_choice_accepts_the_documented_shape_and_its_variants():
    assert read_choice({"answers": {"q": answer("no", 0.8, JUDGE_OPTIONS)}}, "q", JUDGE_OPTIONS)[0] == "no"
    assert read_choice({"answers": {"q": {"choice": "yes", "confidence": 0.7}}}, "q", JUDGE_OPTIONS) == ("yes", {"yes": 0.7})
    assert read_choice({"answers": {"q": {"probabilities": {"yes": 0.2, "no": 0.7, "unknown": 0.1}}}}, "q", JUDGE_OPTIONS)[0] == "no"
    for bad in ({"answers": {}}, {"answers": {"q": {"choice": "maybe"}}}, {"answers": {"q": {"choice": "yes"}}}, []):
        with pytest.raises(BackendError):
            read_choice(bad, "q", JUDGE_OPTIONS)


# -- Clef as the judge -------------------------------------------------------------------
def test_mock_claims_judged_by_clef_reach_their_expected_decisions(tmp_path):
    for claim in ("01-clean-medical-cancellation", "09-prior-knowledge", "10-voluntary-change"):
        loaded = load_claim_dir(ROOT / claim)
        oracle = json.loads(loaded.oracle_path.read_text())
        seen: list = []
        judge = ClefJudge(settings(tmp_path), clef_client(tmp_path, oracle_clef(oracle, seen)))
        backend = Routed(ScriptedBackend(oracle), judge=judge)
        result = process_claim(loaded.input, get_policy(loaded.policy), backend)
        assert result.decision.outcome.value == loaded.expected["decision"], (claim, result.decision.summary)
        judged = [c for c in result.calls if c.component == "judge"]
        assert judged and all(c.model == "clef" and c.call_id and c.ok for c in judged)
        assert {c.model for c in result.calls if c.component != "judge"} == {"scripted"}
        state = seen[0]["state"]
        assert "# The insurance policy" in state and "# Documents" in state and "# Facts already extracted" in state


def test_clef_answers_become_verdicts_with_a_confidence_floor(tmp_path):
    loaded = load_claim_dir(ROOT / "01-clean-medical-cancellation")
    oracle = json.loads(loaded.oracle_path.read_text())

    def handler(request):
        (name, _), = json.loads(request.content)["questions"].items()
        p = 0.55 if name == "prior_knowledge" else 0.9           # prior_knowledge: below the 0.6 floor
        return httpx.Response(200, json={"answers": {name: answer(oracle["judgments"][name]["answer"], p, JUDGE_OPTIONS)}})

    judge = ClefJudge(settings(tmp_path), clef_client(tmp_path, handler))
    result = process_claim(loaded.input, get_policy(loaded.policy), Routed(ScriptedBackend(oracle), judge=judge))
    prior = next(e for e in result.exclusions if e.id == "prior_knowledge")
    assert prior.verdict == Verdict.UNKNOWN and "below the 0.60 floor" in prior.rationale and prior.confidence == 0.55
    assert result.decision.outcome == Outcome.UNCERTAIN


def test_a_failed_clef_call_is_an_unknown_check_and_is_logged(tmp_path):
    loaded = load_claim_dir(ROOT / "01-clean-medical-cancellation")
    oracle = json.loads(loaded.oracle_path.read_text())
    judge = ClefJudge(settings(tmp_path), clef_client(tmp_path, lambda r: httpx.Response(503, text="loading")))
    result = process_claim(loaded.input, get_policy(loaded.policy), Routed(ScriptedBackend(oracle), judge=judge))
    assert result.decision.outcome == Outcome.UNCERTAIN
    failed = [c for c in result.calls if not c.ok]
    assert failed and all(c.component == "judge" and c.model == "clef" and c.call_id for c in failed)
    logged = [json.loads(l) for l in (tmp_path / "trace" / "calls.jsonl").read_text().splitlines()]
    assert {r["provider"] for r in logged} == {"clef"} and all("503" in r["error"] for r in logged)


def test_with_routes_routes_only_when_configured(tmp_path):
    base = ScriptedBackend({})
    assert with_routes(base, Settings()) is base
    routed = with_routes(base, settings(tmp_path, judge_backend="clef"))
    assert isinstance(routed, Routed) and routed.name == "scripted+clef judge"
    with pytest.raises(BackendError):
        with_routes(base, Settings(judge_backend="oracle"))


# -- the Clef strategy -------------------------------------------------------------------
def e2e_handler(seen: list, outcome="APPROVE", coverage="cancellation"):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        q = body["questions"]
        return httpx.Response(200, json={"answers": {
            "outcome": answer(outcome, 0.8, q["outcome"]["criteria"]),
            "coverage": answer(coverage, 0.7, q["coverage"]["criteria"])}})
    return handler


def test_clef_strategy_sends_the_whole_claim_in_one_request(tmp_path):
    loaded = load_claim_dir(ROOT / "08-altered-booking-image")
    policy = get_policy(loaded.policy)
    seen: list = []
    coverage = policy.coverages[0].name
    result = process_claim_clef(loaded.input, policy, clef_client(tmp_path, e2e_handler(seen, coverage=coverage)))
    (body,) = seen
    assert set(body["questions"]) == {"outcome", "coverage"}
    assert set(body["questions"]["outcome"]["criteria"]) == {"APPROVE", "DENY", "UNCERTAIN"}
    assert set(body["questions"]["coverage"]["criteria"]) == {c.name for c in policy.coverages} | {"none"}
    assert body["images"] and all(i.startswith("data:image/") for i in body["images"])
    assert "# The insurance policy" in body["state"] and "(image 1, attached)" in body["state"]
    assert result.strategy == "clef" and result.decision.outcome == Outcome.APPROVE
    assert result.decision.payout is None and result.decision.confidence == pytest.approx(0.8)
    assert result.decision.coverages_applied == [coverage]
    assert [(c.component, c.model, c.ok) for c in result.calls] == [("clef", "clef", True)]
    logged = json.loads((tmp_path / "trace" / "calls.jsonl").read_text().splitlines()[-1])
    assert logged["strategy"] == "clef" and logged["request"]["images"][0]["blob"].startswith("blobs/")


def test_clef_strategy_attaches_at_most_four_images(tmp_path):
    loaded = load_claim_dir(ROOT / "08-altered-booking-image")
    image = next(f for f in loaded.input.files if f.filename.endswith((".png", ".jpg", ".webp")))
    loaded.input.files = [ClaimFile(f"photo{i}.png", image.content) for i in range(MAX_IMAGES + 2)]
    seen: list = []
    result = process_claim_clef(loaded.input, get_policy(loaded.policy),
                                clef_client(tmp_path, e2e_handler(seen, outcome="UNCERTAIN")))
    assert len(seen[0]["images"]) == MAX_IMAGES and "not attached" in seen[0]["state"]
    assert len(result.decision.unknowns) == 2 and "photo5.png" in result.decision.unknowns[-1]


def test_failed_clef_strategy_call_is_an_uncertain_result(tmp_path):
    loaded = load_claim_dir(ROOT / "01-clean-medical-cancellation")
    result = process_claim_clef(loaded.input, get_policy(loaded.policy),
                                clef_client(tmp_path, lambda r: httpx.Response(200, json={"oops": 1})))
    assert result.decision.outcome == Outcome.UNCERTAIN and "no answers" in result.decision.unknowns[0]
    assert result.calls[0].ok is False and result.calls[0].call_id


def test_runner_runs_the_clef_strategy(tmp_path, monkeypatch):
    seen: list = []
    transport = httpx.MockTransport(e2e_handler(seen, outcome="DENY", coverage="none"))
    real_init = ClefClient.__init__

    def init(self, s=None, http=None, log=None):
        real_init(self, s, httpx.Client(base_url="http://clef.local/v1", transport=transport), log)

    monkeypatch.setattr(clef_backend.ClefClient, "__init__", init)
    monkeypatch.setenv("CLAIMS_CLEF_BASE_URL", "http://clef.local/v1")
    m = bench.run(ROOT, tmp_path / "out", None, "clef", False, "t", strategy="clef")
    data = json.loads((tmp_path / "out" / "results.json").read_text())
    assert data["strategy"] == "clef" and m["unprocessed"] == 0 and len(seen) == len(data["claims"])


def test_every_strategy_is_told_what_the_current_date_stamp_means(tmp_path):
    from claims_pipeline.e2e import TASK
    from claims_pipeline.llm.anthropic_backend import ROLE, SUBMISSION_STAMP
    from claims_pipeline.clef_e2e import state_and_images

    assert "Current date is" in SUBMISSION_STAMP and SUBMISSION_STAMP in ROLE    # decomposed and e2e system prompts
    loaded = load_claim_dir(ROOT / "b13-submission-date-only-in-stamp")
    assert loaded.input.submitted_at is None
    state, _, _ = state_and_images(loaded.input, get_policy("cfsr"))
    assert state.startswith(SUBMISSION_STAMP) and "Current date is: 2025-04-21" in state
    seen: list = []
    oracle = json.loads(loaded.oracle_path.read_text())
    judge = ClefJudge(settings(tmp_path), clef_client(tmp_path, oracle_clef(oracle, seen)))
    process_claim(loaded.input, get_policy("cfsr"), Routed(ScriptedBackend(oracle), judge=judge))
    assert all(body["state"].startswith(SUBMISSION_STAMP) for body in seen)
