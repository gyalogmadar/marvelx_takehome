from claims_pipeline.components import agrees_across_documents, days_between_at_most, document_present, within_period, before
from claims_pipeline.components.checks import judged
from claims_pipeline.domain import Document, Trust, Verdict
from claims_pipeline.llm.types import Judgment

from .conftest import make_ctx, make_facts, obs


def test_within_period_pass_fail_unknown(facts_ok):
    assert within_period("incident_date", "policy_period").run(make_ctx(facts_ok)).verdict == Verdict.PASS
    early = make_facts(incident_date="2025-01-28", policy_period={"start": "2025-02-01", "end": "2025-04-10"})
    r = within_period("incident_date", "policy_period").run(make_ctx(early))
    assert r.verdict == Verdict.FAIL
    assert "doc-one.md" in r.rationale and "incident date" in r.rationale
    assert sorted(r.facts_used) == ["incident_date", "policy_period"]
    missing = make_facts(incident_date="2025-01-28")
    r = within_period("incident_date", "policy_period").run(make_ctx(missing))
    assert r.verdict == Verdict.UNKNOWN and "policy period" in r.rationale


def test_days_between_boundary_and_negative_gap():
    base = {"policy_period": {"start": "2025-02-01", "end": "2025-04-10"}}
    chk = days_between_at_most("policy_period.end", "claim_submitted", 30)
    assert chk.run(make_ctx(make_facts(claim_submitted="2025-05-10", **base))).verdict == Verdict.PASS
    assert chk.run(make_ctx(make_facts(claim_submitted="2025-05-11", **base))).verdict == Verdict.FAIL
    r = chk.run(make_ctx(make_facts(claim_submitted="2025-03-01", **base)))
    assert r.verdict == Verdict.PASS and "before" in r.rationale


def test_before_check():
    f = make_facts(purchase_date="2025-02-01", incident_date="2025-03-20")
    assert before("purchase_date", "incident_date").run(make_ctx(f)).verdict == Verdict.PASS
    assert before("incident_date", "purchase_date").run(make_ctx(f)).verdict == Verdict.FAIL


def test_agreement_check():
    agree = make_facts(incident_date=[obs("d1", "2025-03-12"), obs("d2", "2025-03-12")])
    assert agrees_across_documents("incident_date").run(make_ctx(agree)).verdict == Verdict.PASS
    disagree = make_facts(incident_date=[obs("d1", "2025-03-12"), obs("d2", "2025-03-20")])
    assert agrees_across_documents("incident_date").run(make_ctx(disagree)).verdict == Verdict.FAIL
    assert agrees_across_documents("incident_date").run(make_ctx(make_facts())).verdict == Verdict.UNKNOWN


def test_document_present_ignores_untrusted_and_flags_unreadable():
    docs = [Document(id="a", filename="r.md", kind="markdown", doc_type="police_report")]
    assert document_present("police_report").run(make_ctx(make_facts(), docs)).verdict == Verdict.PASS
    docs[0].trust = Trust.UNTRUSTED
    assert document_present("police_report").run(make_ctx(make_facts(), docs)).verdict == Verdict.FAIL
    docs.append(Document(id="b", filename="x.pdf", kind="text", doc_type="unreadable"))
    assert document_present("police_report").run(make_ctx(make_facts(), docs)).verdict == Verdict.UNKNOWN


class FakeBackend:
    name = "fake"
    last_usage = None

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def judge(self, req):
        self.requests.append(req)
        return Judgment(answer=self.answer, quote="q", document_id="a", rationale="because", confidence=0.8)


def test_judged_maps_answers_onto_verdicts():
    docs = [Document(id="a", filename="r.md", kind="markdown", doc_type="police_report", lines=["q"])]
    chk = judged("prior_knowledge", "Did they know?", expect="no", facts=("incident_date",))
    assert chk.run(make_ctx(make_facts(), docs, FakeBackend("no"))).verdict == Verdict.PASS
    r = chk.run(make_ctx(make_facts(), docs, FakeBackend("yes")))
    assert r.verdict == Verdict.FAIL and r.kind == "judged" and r.quote == "q" and r.document_ids == ["a"]
    assert r.facts_used == ["incident_date"] and r.confidence == 0.8
    assert chk.run(make_ctx(make_facts(), docs, FakeBackend("unknown"))).verdict == Verdict.UNKNOWN
