"""The Anthropic backend and the e2e strategy, with the SDK call replaced by a fake.

These tests cover prompt assembly and the mapping of structured outputs back
into the result tree.  Nothing here talks to the network.
"""
from datetime import datetime, timezone
from pathlib import Path

from claims_pipeline.benchmark.compare import compare, markdown
from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.domain import Outcome
from claims_pipeline.e2e import E2EOut, DocOut, EvidenceOut, build_content, process_claim_e2e
from claims_pipeline.llm.anthropic_backend import (
    AnthropicBackend, AnthropicClient, ExplainOut, ExtractOut, JudgmentOut, ObservationOut, ReadOut,
)
from claims_pipeline.llm.config import Settings
from claims_pipeline.llm.types import Usage
from claims_pipeline.pipeline import ClaimFile, ClaimInput, process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


class FakeApi(AnthropicClient):
    """Returns canned structured outputs per component and records every prompt."""

    def __init__(self, answers):
        self.settings = Settings()
        self.answers = answers
        self.calls = []

    def parse(self, component, system, content, output_model):
        self.calls.append((component, system, content, output_model))
        answer = self.answers[component]
        out = answer(content) if callable(answer) else answer
        assert isinstance(out, output_model)
        return out, Usage(model="fake", input_tokens=1000, output_tokens=50, cache_read_tokens=800)


def make_claim(name="01-clean-medical-cancellation"):
    loaded = load_claim_dir(ROOT / name)
    return loaded, get_policy(loaded.policy)


def test_decomposed_pipeline_through_anthropic_backend():
    loaded, policy = make_claim()

    def read(content):
        filename = content[0]["text"].split("\n")[0].replace("Filename: ", "")
        return ReadOut(doc_type="medical_report" if "hospital" in filename else "booking_confirmation",
                       language="en", issuer="X", lines=None, alteration_signs=[], summary="s")

    def extract(content):
        text = content[0]["text"]
        assert "# Documents" in text and "[id=d1-booking-confirmation" in text
        return ExtractOut(observations=[
            ObservationOut(fact="incident_date", document_id="d2-hospital-certificate", line=4, quote="Admitted on 2025-03-20", value="2025-03-20", confidence=0.95),
            ObservationOut(fact="policy_period", document_id="d1-booking-confirmation", line=9, quote="CFSR cover period: 2025-02-01 to 2025-04-10", value="2025-02-01/2025-04-10", confidence=0.9),
            ObservationOut(fact="purchase_date", document_id="d1-booking-confirmation", line=8, quote="Booked and paid on 2025-02-01", value="2025-02-01", confidence=0.9),
            ObservationOut(fact="booking_value", document_id="d1-booking-confirmation", line=7, quote="Total paid: EUR 1,240.00", value="1240", confidence=0.98),
            ObservationOut(fact="passengers", document_id="d1-booking-confirmation", line=6, quote="Passengers: 1", value="1", confidence=0.9),
            ObservationOut(fact="affected", document_id="description", line=1, quote="My name is Anna Weber.", value="1", confidence=0.8),
            ObservationOut(fact="claimant_name", document_id="description", line=1, quote="My name is Anna Weber.", value="Anna Weber", confidence=0.99),
            ObservationOut(fact="nonsense", document_id="d1-booking-confirmation", line=1, quote="x", value="y", confidence=0.5),  # dropped
        ])

    def judge(content):
        q = content[0]["text"].split("\n")[1]
        yes_questions = ("cancelled or rescheduled", "supporting document confirm")
        answer = "yes" if any(k in q for k in yes_questions) else "no"
        return JudgmentOut(answer=answer, document_filename="hospital-certificate.md", line=5, quote="Diagnosis: acute appendicitis", rationale="because", confidence=0.9)

    api = FakeApi({"read_document": read, "extract_facts": extract, "judge": judge, "explain": ExplainOut(text="Dear Anna, ...")})
    result = process_claim(loaded.input, policy, AnthropicBackend(Settings(), api))

    assert result.decision.outcome == Outcome.APPROVE and result.decision.payout == 1240.0
    assert result.decision.explanation == "Dear Anna, ..."
    assert {c.component for c in result.calls} == {"read_document", "extract_facts", "judge", "explain"}
    assert all(c.cache_read_tokens == 800 and c.cost_usd == 0 for c in result.calls)  # 'fake' model has no price
    assert result.fact("policy_period").value == {"start": "2025-02-01", "end": "2025-04-10"}
    assert result.fact("nonsense") is None
    # the policy text sits in every system prompt after the first document read, so it can be cached
    systems = [s for comp, s, _, _ in api.calls if comp != "read_document"]
    assert all("Cancellation For Specific Reason" in s for s in systems)
    judged = next(r for r in result.coverages[0].checks if r.kind == "judged")
    assert judged.document_ids == ["d2-hospital-certificate"] and judged.quote == "Diagnosis: acute appendicitis"


def test_read_document_sends_images_and_keeps_text_lines():
    loaded, policy = make_claim("08-altered-booking-image")
    seen = {}

    def read(content):
        kinds = [b["type"] for b in content]
        seen[content[0]["text"].split("\n")[0]] = kinds
        if "image" in kinds:
            return ReadOut(doc_type="booking_confirmation", language="en", lines=["Booking confirmation ET-77588", "Total paid: EUR 2,180.00"],
                           alteration_signs=["retyped total", "overwritten digit"], summary="photo")
        return ReadOut(doc_type="medical_report", language="pt", lines=["ignored for text docs"], alteration_signs=[], summary="letter")

    api = FakeApi({"read_document": read, "extract_facts": ExtractOut(observations=[]),
                   "judge": JudgmentOut(answer="unknown", rationale="?", confidence=0.3), "explain": ExplainOut(text="")})
    result = process_claim(loaded.input, policy, AnthropicBackend(Settings(), api))
    assert seen["Filename: booking-photo.png"] == ["text", "image"]
    img = next(d for d in result.documents if d.kind == "image")
    assert img.lines[0] == "Booking confirmation ET-77588" and img.trust.value == "untrusted"
    txt = next(d for d in result.documents if d.filename == "hospital-letter.md")
    assert txt.lines[0].startswith("# Medical certificate") and txt.language == "pt"   # file lines kept, model lines ignored


def test_e2e_strategy_builds_one_prompt_and_fills_the_tree():
    loaded, policy = make_claim("12-missed-connection-formula")
    api = FakeApi({"e2e": E2EOut(outcome="APPROVE", payout=200, confidence=0.85, summary="Approved: missed connection after a taxi accident.",
                                 reasoning="The accident report ...", evidence=[EvidenceOut(document="accident-report.md", quote="road traffic collision", relevance="cause")],
                                 concerns=[], documents=[DocOut(filename="accident-report.md", doc_type="incident_report", language="en")])})
    result = process_claim_e2e(loaded.input, policy, api)
    assert result.strategy == "e2e" and result.decision.outcome == Outcome.APPROVE and result.decision.payout == 200.0
    assert "Key evidence" in result.decision.explanation and result.facts == [] and result.coverages == []
    assert [c.component for c in result.calls] == ["e2e"]
    component, system, content, model = api.calls[0]
    assert "Cancellation For Specific Reason" in system and model is E2EOut
    texts = [b["text"] for b in content if b["type"] == "text"]
    assert texts[0].startswith("# Claim description") and "submitted: 2025-03-06" in texts[0]
    assert any("# Document: accident-report.md" in t and "1: # Police report" in t for t in texts)
    assert next(d for d in result.documents if d.filename == "accident-report.md").doc_type == "incident_report"


def test_e2e_content_includes_images_and_marks_binary():
    claim = ClaimInput(claim_id="x", description="d", files=[ClaimFile("a.png", b"\x89PNG"), ClaimFile("b.pdf", b"%PDF\xff\xfe")],
                       submitted_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    content = build_content(claim)
    assert [b["type"] for b in content] == ["text", "text", "image", "text"]
    assert content[2]["source"]["media_type"] == "image/png" and "could not be read" in content[3]["text"]


def test_compare_report(tmp_path):
    import json
    a = {"name": "a", "title": "A", "strategy": "decomposed", "claims": [
        {"claim_id": "c1", "expected": "APPROVE", "expected_payout": 100.0, "actual": "APPROVE", "actual_payout": 100.0, "match": True, "summary": "ok"},
        {"claim_id": "c2", "expected": "DENY", "expected_payout": None, "actual": "UNCERTAIN", "actual_payout": None, "match": False, "summary": "hmm"}],
         "metrics": {"accuracy": 0.5, "correct": 1, "scored": 2, "claims": 2, "payout": {"exact": 1, "scored": 1}, "total_cost_usd": 0.5,
                     "components": {"judge": {"calls": 4, "latency_ms": 4000}}, "confusion": {}, "per_outcome": {}}}
    b = json.loads(json.dumps(a)); b.update(name="b", title="B", strategy="e2e")
    b["claims"][1].update(actual="DENY", match=True, summary="denied")
    b["metrics"].update(accuracy=1.0, correct=2, total_cost_usd=0.2, components={"e2e": {"calls": 2, "latency_ms": 9000}})
    c = compare(a, b)
    assert c["agreement"] == {"both_right": 1, "only_a_right": 0, "only_b_right": 1, "neither": 0, "same_decision": 1, "claims": 2}
    md = markdown(c)
    assert "| c2 | DENY | UNCERTAIN | DENY ✓ |" in md and "A: hmm" in md
