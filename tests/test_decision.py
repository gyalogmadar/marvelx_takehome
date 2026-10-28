"""The decision rule, exercised directly with hand-built check results."""
from claims_pipeline.components import Coverage, Policy, all_of, any_of, formula, judged
from claims_pipeline.domain import CheckResult, CoverageResult, Outcome, Verdict

from .conftest import make_facts


def check(id, verdict, kind="mechanical", conf=0.9):
    return CheckResult(id=id, kind=kind, description=id, verdict=verdict, rationale=f"{id} {verdict.value}", confidence=conf)


def coverage(name, status, payout=None, match=Verdict.PASS, proofs=Verdict.PASS, checks=(), payout_note=None):
    return CoverageResult(name=name, title=name, match=check(f"{name}_match", match, "judged"),
                          proofs=check(f"proofs:{name}", proofs, "proof"), checks=list(checks), status=status,
                          payout=payout, payout_note=payout_note)


POLICY = Policy(name="t", title="t", text="", facts=[], doc_types={}, coverages=[], exclusions=[])


def test_failed_exclusion_denies_even_when_coverage_is_eligible():
    d = POLICY.decide([check("late", Verdict.FAIL)], [coverage("c", "eligible", 100.0)], make_facts())
    assert d.outcome == Outcome.DENY and d.determining == ["late"] and d.payout is None


def test_all_not_applicable_is_not_a_covered_reason():
    d = POLICY.decide([check("x", Verdict.PASS)], [coverage("a", "not_applicable", match=Verdict.FAIL),
                                                   coverage("b", "not_applicable", match=Verdict.FAIL)], make_facts())
    assert d.outcome == Outcome.DENY and "not covered" in d.summary


def test_eligible_coverages_add_up():
    d = POLICY.decide([check("x", Verdict.PASS)], [coverage("a", "eligible", 100.0), coverage("b", "eligible", 150.0),
                                                   coverage("c", "not_applicable", match=Verdict.FAIL)], make_facts())
    assert d.outcome == Outcome.APPROVE and d.payout == 250.0 and d.coverages_applied == ["a", "b"]


def test_open_exclusion_blocks_approval():
    d = POLICY.decide([check("prior", Verdict.UNKNOWN, "judged")], [coverage("a", "eligible", 100.0)], make_facts())
    assert d.outcome == Outcome.UNCERTAIN and d.determining == ["prior"]
    assert d.unknowns[0].startswith("The claim is otherwise eligible")


def test_only_ineligible_denies():
    d = POLICY.decide([check("x", Verdict.PASS)], [coverage("a", "ineligible", proofs=Verdict.FAIL),
                                                   coverage("b", "not_applicable", match=Verdict.FAIL)], make_facts())
    assert d.outcome == Outcome.DENY and d.determining == ["proofs:a"]


def test_unresolved_coverage_refers():
    d = POLICY.decide([check("x", Verdict.PASS)], [coverage("a", "unresolved", match=Verdict.UNKNOWN)], make_facts())
    assert d.outcome == Outcome.UNCERTAIN and "a_match" in d.determining


def test_unknown_match_gates_proof_failure():
    """A coverage whose match is unknown stays unresolved even when its proofs would fail."""
    cov = Coverage(name="c", title="c", matches=judged("m", "?"), proofs=all_of("police_report"), payout=formula("100"))

    class Unknown:
        name = "u"; last_usage = None

        def judge(self, req):
            from claims_pipeline.llm.types import Judgment
            return Judgment(answer="unknown", rationale="cannot tell", confidence=0.4)

    from .conftest import make_ctx
    r = cov.evaluate(make_ctx(make_facts(), [], Unknown()))
    assert r.status == "unresolved" and r.proofs.verdict == Verdict.FAIL and r.checks == []


def test_proof_rules_compose():
    rule = all_of(any_of("medical_report", "police_report"), "booking_confirmation")
    assert rule.describe() == "(medical_report or police_report) and booking_confirmation"
    assert rule.doc_types() == {"medical_report", "police_report", "booking_confirmation"}
