from datetime import date

from claims_pipeline.components import Date, Money, Period, Int

from .conftest import make_facts, obs


def test_money_parses_european_and_english_formats():
    m = Money("x")
    assert m.parse("EUR 1.200,50") == 1200.50
    assert m.parse("€1,200.50") == 1200.50
    assert m.parse("1200,5") == 1200.5
    assert m.parse(1240) == 1240.0
    assert m.parse("n/a") is None


def test_date_and_period_parse():
    assert Date("d").parse("2025-03-20T10:00:00") == date(2025, 3, 20)
    assert Date("d").parse("20 March 2025") is None  # the extractor must normalise to ISO
    assert Period("p").parse({"start": "2025-02-01", "end": "2025-04-10"}) == (date(2025, 2, 1), date(2025, 4, 10))
    assert Period("p").parse("2025-02-01/2025-04-10") is not None
    assert Period("p").parse({"start": "2025-05-01", "end": "2025-04-10"}) is None  # end before start


def test_int_parses_text():
    assert Int("n").parse("4 passengers") == 4
    assert Int("n").parse(True) is None


def test_consolidation_keeps_all_observations_and_flags_disagreement():
    facts = make_facts(incident_date=[obs("d1", "2025-03-12", 0.95), obs("d2", "2025-03-20", 0.9)])
    f = facts.get("incident_date")
    assert f.value == "2025-03-12"            # the higher-confidence group wins
    assert f.note.startswith("observations disagree")
    assert len(f.observations) == 2
    assert f.confidence < 0.95                # disagreement costs confidence


def test_consolidation_agreement_takes_max_confidence():
    facts = make_facts(incident_date=[obs("d1", "2025-03-12", 0.7), obs("d2", "2025-03-12", 0.95)])
    f = facts.get("incident_date")
    assert f.value == "2025-03-12" and f.confidence == 0.95 and f.note is None


def test_typed_access_to_period_parts_and_sources():
    facts = make_facts(policy_period={"start": "2025-02-01", "end": "2025-04-10"})
    assert facts.typed("policy_period.end") == date(2025, 4, 10)
    assert facts.typed("policy_period.start") == date(2025, 2, 1)
    assert facts.typed("incident_date") is None
    assert facts.source("policy_period") == "doc-one.md"
    assert facts.display("incident_date") == "incident date (unknown)"
