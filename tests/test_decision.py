"""The decision rule, exercised directly with hand-built check results."""
from claims_pipeline.components import Coverage, Policy, all_of, any_of, formula, judged
from claims_pipeline.domain import CheckResult, CoverageResult, Observation, Outcome, Reading, Verdict

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


def test_an_open_exclusion_does_not_hold_up_a_denial_when_nothing_can_pay():
    """Exclusions only take cover away: with no coverage able to pay, an unknown one is moot."""
    d = POLICY.decide([check("prior", Verdict.UNKNOWN, "judged")],
                      [coverage("a", "ineligible", proofs=Verdict.FAIL), coverage("b", "not_applicable", match=Verdict.FAIL)],
                      make_facts())
    assert d.outcome == Outcome.DENY and d.determining == ["proofs:a"]


def test_unresolved_coverage_refers():
    d = POLICY.decide([check("x", Verdict.PASS)], [coverage("a", "unresolved", match=Verdict.UNKNOWN)], make_facts())
    assert d.outcome == Outcome.UNCERTAIN and "a_match" in d.determining


def test_unknown_match_is_unresolved_unless_the_coverage_could_never_pay():
    """A coverage whose match is unknown is left for a human, unless its required proof is missing:
    then it cannot pay whichever way the question goes, and it is ineligible."""
    cov = Coverage(name="c", title="c", matches=judged("m", "?"), proofs=all_of("police_report"), payout=formula("100"))

    class Unknown:
        name = "u"; last_usage = None

        def judge(self, req):
            from claims_pipeline.llm.types import Judgment
            return Judgment(answer="unknown", rationale="cannot tell", confidence=0.4)

    from .conftest import make_ctx
    r = cov.evaluate(make_ctx(make_facts(), [], Unknown()))
    assert r.status == "ineligible" and r.proofs.verdict == Verdict.FAIL and r.checks == [] and r.payout is None
    police = Reading(id="d1", filename="r.md", kind="markdown", doc_type="police_report")
    r = cov.evaluate(make_ctx(make_facts(), [police], Unknown()))
    assert r.status == "unresolved" and r.proofs.verdict == Verdict.PASS


def test_proof_rules_compose():
    rule = all_of(any_of("medical_report", "police_report"), "booking_confirmation")
    assert rule.describe() == "(medical_report or police_report) and booking_confirmation"
    assert rule.doc_types() == {"medical_report", "police_report", "booking_confirmation"}


# -- red flags: refer, never deny ------------------------------------------------------
def test_a_failing_red_flag_refers_an_otherwise_approvable_claim():
    d = POLICY.decide([check("x", Verdict.PASS)], [coverage("a", "eligible", 100.0)], make_facts(),
                      red_flags=[check("suspicious", Verdict.FAIL, "judged")])
    assert d.outcome == Outcome.UNCERTAIN and d.payout is None
    assert d.determining == ["suspicious"] and d.unknowns[0].startswith("Red flag:")


def test_a_red_flag_never_overturns_a_denial():
    d = POLICY.decide([check("late", Verdict.FAIL)], [coverage("a", "eligible", 100.0)], make_facts(),
                      red_flags=[check("suspicious", Verdict.FAIL, "judged")])
    assert d.outcome == Outcome.DENY and d.determining == ["late"]


def test_an_open_red_flag_blocks_approval_and_a_passing_one_does_not():
    open_flag = POLICY.decide([], [coverage("a", "eligible", 100.0)], make_facts(),
                              red_flags=[check("suspicious", Verdict.UNKNOWN, "judged")])
    assert open_flag.outcome == Outcome.UNCERTAIN
    clean = POLICY.decide([], [coverage("a", "eligible", 100.0)], make_facts(),
                          red_flags=[check("suspicious", Verdict.PASS, "judged")])
    assert clean.outcome == Outcome.APPROVE and clean.payout == 100.0


# -- review signals: shown where they bear on the decision, never changing it --------------
def flagged(id, verdict, why, facts_used=()):
    return check(id, verdict, "judged").model_copy(update={"review": why, "facts_used": list(facts_used)})


def test_a_flagged_reading_is_always_shown_and_the_denial_stands():
    letter = Reading(id="d1", filename="booking.md", kind="markdown", review="the trip was already refunded")
    d = POLICY.decide([check("late", Verdict.FAIL)], [coverage("c", "eligible", 100.0)], make_facts(), readings=[letter])
    assert d.outcome == Outcome.DENY and d.payout is None
    assert d.reviews == ["read_document · booking.md: the trip was already refunded"]


def test_a_judge_flag_is_shown_only_when_the_decision_rests_on_it():
    deny_on_late = [check("late", Verdict.FAIL), flagged("prior", Verdict.PASS, "is a relapse 'known'?")]
    d = POLICY.decide(deny_on_late, [coverage("c", "eligible", 100.0)], make_facts())
    assert d.outcome == Outcome.DENY and d.reviews == []                       # the denial rests on 'late' alone
    approve = [check("late", Verdict.PASS), flagged("prior", Verdict.PASS, "is a relapse 'known'?")]
    d = POLICY.decide(approve, [coverage("c", "eligible", 100.0)], make_facts())
    assert d.outcome == Outcome.APPROVE and d.payout == 100.0 and d.reviews == ["judge · prior: is a relapse 'known'?"]


def test_a_flagged_fact_is_shown_on_the_checks_that_used_it():
    facts = make_facts(affected=[Observation(document_id="d1", line=1, quote="q", value=4, confidence=0.6,
                                             review="4 missed the train, 2 were injured")])
    approve = [check("late", Verdict.PASS).model_copy(update={"facts_used": ["affected"]})]
    d = POLICY.decide(approve, [coverage("c", "eligible", 100.0)], facts)
    assert d.outcome == Outcome.APPROVE and d.reviews == ["extract_facts · affected: 4 missed the train, 2 were injured"]


def test_an_uncertain_decision_lists_every_flag_for_the_person_who_gets_it():
    d = POLICY.decide([flagged("prior", Verdict.UNKNOWN, "wording unclear")], [coverage("c", "eligible", 100.0)],
                      make_facts())
    assert d.outcome == Outcome.UNCERTAIN and d.reviews == ["judge · prior: wording unclear"]
