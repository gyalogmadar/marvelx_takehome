"""The store holds the whole proof chain: every result saved and loaded back is identical."""
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.domain import ClaimResult
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy
from claims_pipeline.store import models as m
from claims_pipeline.store.db import alembic_config, upgrade
from claims_pipeline.store.repository import add_claim, connect, load_run, save_run, snapshot_policy, snapshot_prompts

PROJECT = Path(__file__).resolve().parents[1]
MOCKS = sorted(p for p in (PROJECT / "mockclaims").iterdir() if (p / "oracle.json").exists())
# results the local model produced on the mock claims, when this checkout has them
RECORDED = sorted(p for run in ("mock-decomposed", "mock-decomposed-clef", "mock-e2e", "mock-clef-e2e")
                  for p in (PROJECT / "results" / run).glob("*.json") if p.name != "results.json")


def migrated(url: str) -> Config:
    """Build the database the way a deployment does: alembic upgrade head."""
    upgrade(url)
    return alembic_config(url)


@pytest.fixture
def session(tmp_path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    migrated(url)
    with Session(connect(url)) as s:
        yield s


def test_the_migrations_build_exactly_the_tables_in_models(tmp_path):
    cfg = migrated(f"sqlite:///{tmp_path / 'check.db'}")
    command.check(cfg)                                # raises if models.py and the migrations differ


def round_trip(s: Session, loaded, result: ClaimResult) -> ClaimResult:
    claim = add_claim(s, loaded.input, loaded.policy)
    run_id = save_run(s, result, claim_id=claim.id, policy_id=snapshot_policy(s, get_policy(loaded.policy)))
    s.commit()
    s.expunge_all()                                   # load from the tables, not from the session's objects
    return load_run(s, run_id)


@pytest.mark.parametrize("folder", MOCKS, ids=lambda p: p.name)
def test_every_mock_result_round_trips(session, folder):
    loaded = load_claim_dir(folder)
    result = process_claim(loaded.input, get_policy(loaded.policy), ScriptedBackend.from_file(loaded.oracle_path))
    assert round_trip(session, loaded, result) == result


@pytest.mark.skipif(not RECORDED, reason="no recorded local-model results in results/")
def test_every_recorded_local_model_result_round_trips(session):
    for path in RECORDED:
        result = ClaimResult.model_validate_json(path.read_text())
        loaded = load_claim_dir(PROJECT / "mockclaims" / result.claim_id)
        with Session(session.get_bind()) as s:
            claim = add_claim(s, loaded.input, loaded.policy)
            run_id = save_run(s, result, claim_id=claim.id)
            s.commit()
        with Session(session.get_bind()) as s:
            assert load_run(s, run_id) == result, path


def test_text_files_are_not_copied_and_transcripts_are(session):
    loaded = load_claim_dir(PROJECT / "mockclaims" / "08-altered-booking-image")
    result = process_claim(loaded.input, get_policy(loaded.policy), ScriptedBackend.from_file(loaded.oracle_path))
    round_trip(session, loaded, result)
    rows = {r.key: r for r in session.query(m.Reading)}
    assert rows["d2-hospital-letter"].transcript is None and rows["d2-hospital-letter"].document_id is not None
    assert rows["d1-booking-photo"].transcript                     # the reader's transcription of the image
    assert rows["description"].document_id is None and rows["description"].transcript is None


def test_the_policy_and_prompts_are_snapshotted_once_per_version(session):
    cfsr = get_policy("cfsr")
    pid = snapshot_policy(session, cfsr)
    assert snapshot_policy(session, cfsr) == pid and session.query(m.Policy).count() == 1
    row = session.get(m.Policy, pid)
    prior = next(c for c in row.checks if c.check_id == "prior_knowledge")
    assert prior.section == "exclusion" and prior.kind == "judged" and prior.expect == "no"
    assert prior.question.startswith("Did the claimant know")
    assert {c.name for c in row.coverages} == {c.name for c in cfsr.coverages}
    ids = snapshot_prompts(session)
    assert snapshot_prompts(session) == ids and session.query(m.Prompt).count() == len(ids)


def test_a_claim_is_written_once_and_changed_content_is_a_new_claim(session):
    loaded = load_claim_dir(PROJECT / "mockclaims" / "01-clean-medical-cancellation")
    first = add_claim(session, loaded.input, "cfsr")
    assert add_claim(session, loaded.input, "cfsr") is first      # identical: the stored claim
    loaded.input.description += " (edited)"
    edited = add_claim(session, loaded.input, "cfsr")
    assert edited.id != first.id and edited.name == first.name == "01-clean-medical-cancellation"
    assert first.description != edited.description                 # the first one is untouched
