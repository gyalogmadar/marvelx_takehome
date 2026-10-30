from __future__ import annotations

import os
from datetime import date

import pytest

from claims_pipeline.components import CheckContext, Date, Facts, Int, Money, Period, Text
from claims_pipeline.domain import Reading, Observation
from claims_pipeline.settings import Settings

# The repo's .env is the deployment's config (backend, server, thinking); tests never read it.
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True, scope="session")
def _scratch_database(tmp_path_factory):
    """Anything that opens the default store during tests opens a scratch database, never data/."""
    os.environ["CLAIMS_DATABASE_URL"] = f"sqlite:///{tmp_path_factory.mktemp('db') / 'pipeline.db'}"
    yield
    os.environ.pop("CLAIMS_DATABASE_URL", None)

SPECS = [Date("incident_date"), Date("purchase_date"), Period("policy_period"), Date("claim_submitted"),
         Money("booking_value"), Int("passengers"), Int("affected"), Text("claimant_name")]


def obs(doc: str, value, confidence: float = 0.9, line: int = 1, quote: str = "q") -> Observation:
    return Observation(document_id=doc, line=line, quote=quote, value=value, confidence=confidence)


def make_facts(**values) -> Facts:
    """Build Facts from name=value (single observation each) or name=[Observation, ...]."""
    observations = []
    for name, v in values.items():
        if isinstance(v, list):
            observations.extend((name, o) for o in v)
        else:
            observations.append((name, obs("d1", v)))
    return Facts.build(SPECS, observations, labels={"d1": "doc-one.md", "d2": "doc-two.md"})


def make_ctx(facts: Facts, readings: list[Reading] | None = None, backend=None) -> CheckContext:
    return CheckContext(facts=facts, readings=readings or [], backend=backend, policy_text="", description="")


@pytest.fixture
def facts_ok() -> Facts:
    return make_facts(incident_date="2025-03-20", purchase_date="2025-02-01",
                      policy_period={"start": "2025-02-01", "end": "2025-04-10"}, claim_submitted="2025-03-25",
                      booking_value="EUR 1,240.00", passengers=1, affected=1, claimant_name="Anna Weber")


@pytest.fixture(autouse=True)
def isolate_call_log(tmp_path, monkeypatch):
    """No test writes the call log under the repo's data/."""
    monkeypatch.setenv("CLAIMS_TRACE_DIR", str(tmp_path / "trace"))
