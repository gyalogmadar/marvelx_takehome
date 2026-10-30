"""Making a run and keeping it: what the API and the benchmark runner call.

    record_run(sessions, claim, policy, settings, strategy=..., backend=..., process=...)

1. snapshots the policy and the prompts, stores the run config, the claim and its
   documents (each once), and opens a run;
2. calls ``process()`` inside ``run_scope``, so every model call is written to the
   run as it happens;
3. saves the result tree into the run, or marks the run failed with the error.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from claims_pipeline.domain import ClaimResult
from claims_pipeline.llm.calllog import run_scope
from claims_pipeline.llm.config import Settings

from .repository import add_claim, fail_run, save_run, snapshot_policy, snapshot_prompts, start_run
from .runconfig import build_config, save_config

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import sessionmaker

    from claims_pipeline.components import Policy
    from claims_pipeline.pipeline import ClaimInput


def current_config(s, settings: Settings, *, strategy: str, backend: str, explain: bool = True) -> str:
    """The config runs made now with these settings are recorded under (stored once)."""
    return save_config(s, build_config(settings, strategy=strategy, backend=backend, prompt_ids=snapshot_prompts(s),
                                       explain=explain))


def prepare_run(sessions: "sessionmaker", claim: "ClaimInput", policy: "Policy", settings: Settings, *,
                strategy: str, backend: str, explain: bool = True, batch_id: int | None = None,
                origin: str = "submitted") -> str:
    """Store what the run needs and open it; return the run id."""
    with sessions() as s, s.begin():
        policy_id = snapshot_policy(s, policy)
        config_id = current_config(s, settings, strategy=strategy, backend=backend, explain=explain)
        row = add_claim(s, claim, policy.name, origin)
        return start_run(s, row.id, strategy, config_id=config_id, policy_id=policy_id, batch_id=batch_id)


def finish_run(sessions: "sessionmaker", run_id: str, process: Callable[[], ClaimResult]) -> ClaimResult:
    """Run ``process`` as run ``run_id`` and keep what it produced (or why it failed)."""
    try:
        with run_scope(run_id, sessions):
            result = process()
    except Exception as exc:
        with sessions() as s, s.begin():
            fail_run(s, run_id, f"{type(exc).__name__}: {exc}")
        raise
    with sessions() as s, s.begin():
        save_run(s, result, run_id=run_id)
    return result


def record_run(sessions: "sessionmaker", claim: "ClaimInput", policy: "Policy", settings: Settings, *,
               strategy: str, backend: str, process: Callable[[], ClaimResult], explain: bool = True,
               batch_id: int | None = None) -> tuple[str, ClaimResult]:
    run_id = prepare_run(sessions, claim, policy, settings, strategy=strategy, backend=backend, explain=explain,
                         batch_id=batch_id)
    return run_id, finish_run(sessions, run_id, process)
