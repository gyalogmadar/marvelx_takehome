"""Replay: run a stored run again with no model, from the outputs its model calls recorded.

    replay_run(sessions, run_id) -> ClaimResult
    python -m claims_pipeline.replay --list    # the recorded batches
    python -m claims_pipeline.replay 3 7       # replay batches 3 and 7, list what moves

Each component asks the replay client instead of a model; the client answers every
call with the next recorded output for that component, in the order they were made.
Under the code the run was made with, the replay rebuilds its result tree exactly.
Under today's code, it shows what a change does to past decisions with the model's
answers held fixed: change a rule in ``decide()``, replay last week's runs, compare.

A call the run never made (a check that did not exist then) gets no answer and is
treated like a failed call: the component degrades to unknown, as it would live.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from claims_pipeline.domain import ClaimResult
from claims_pipeline.llm.base import BackendError
from claims_pipeline.llm.types import Usage
from claims_pipeline.policies import get_policy
from claims_pipeline.settings import Settings

from claims_pipeline.store import models as m
from claims_pipeline.store.repository import load_claim

T = TypeVar("T", bound=BaseModel)


class ReplayClient:
    """Stands in for the model clients: ``parse`` (Claude, OpenAI-compatible) and ``ask`` (Clef)."""

    name = "replay"

    def __init__(self, calls: list[m.ModelCall], settings: Settings):
        self.settings = settings
        self.queues: dict[str, deque[m.ModelCall]] = defaultdict(deque)
        for call in calls:
            self.queues[call.component].append(call)

    def _next(self, component: str) -> tuple[m.ModelCall, Usage]:
        if not self.queues[component]:
            raise BackendError(f"replay: the run made no further {component} call")
        call = self.queues[component].popleft()
        usage = Usage(model=call.model, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                      cache_read_tokens=call.cache_read_tokens, reasoning_tokens=call.reasoning_tokens,
                      call_id=call.call_id)
        if not call.ok:
            exc = BackendError(call.error or "the recorded call failed")
            exc.usage = usage
            raise exc
        return call, usage

    def parse(self, component: str, system: str, content: list[dict[str, Any]], output_model: type[T]) -> tuple[T, Usage]:
        call, usage = self._next(component)
        return output_model.model_validate(call.parsed), usage

    def ask(self, component: str, state: str, questions: dict[str, Any], images: list[str] | None = None):
        call, usage = self._next(component)
        return call.response, usage


def replay_run(sessions: sessionmaker, run_id: str) -> ClaimResult:
    from claims_pipeline.strategies import strategy

    with sessions() as s:
        run = s.get(m.Run, run_id)
        if run is None:
            raise LookupError(f"no run {run_id!r}")
        config = s.get(m.RunConfig, run.config_id).config if run.config_id else {}
        claim = load_claim(run.claim)
        policy = get_policy(run.claim.policy)
        calls = list(run.calls)
        name = run.strategy

    # runs imported from before the store recorded no settings: today's defaults stand in
    how = config.get("run", {})
    settings = Settings(**{**config.get("settings", {}), "trace": False,
                           "judge_backend": how.get("judge") or "", "scorer": how.get("scorer") or ""})
    process = strategy(settings, name, client=ReplayClient(calls, settings), explain=how.get("explain", True))
    return process(claim, policy)


def replay_batch(sessions: sessionmaker, batch_id: int) -> dict[str, Any]:
    """Replay every finished run of a batch under today's code; score both against the benchmark."""
    from claims_pipeline.benchmark.report import score_row
    from claims_pipeline.benchmark.views import batch_results

    with sessions() as s:
        data = batch_results(s, batch_id)
    if data is None:
        raise LookupError(f"no batch {batch_id}")
    rows = []
    for before in data["claims"]:
        if before.get("error"):
            continue
        after = score_row({k: before[k] for k in ("claim_id", "expected", "expected_payout")},
                          replay_run(sessions, before["run_id"]))
        rows.append({"claim_id": before["claim_id"], "expected": before["expected"],
                     "expected_payout": before["expected_payout"], "before": before, "after": after,
                     "changed": (before["actual"], before["actual_payout"]) != (after["actual"], after["actual_payout"])})
    tally = lambda key, strict: sum(  # noqa: E731
        r[key]["match"] if strict else (r["expected"] is not None and r[key]["actual"] == r["expected"]) for r in rows)
    return {"batch": data, "rows": rows,
            "right": {"before": tally("before", True), "after": tally("after", True)},
            "decisions_right": {"before": tally("before", False), "after": tally("after", False)}}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Replay recorded batches under today's code and show what moves.")
    ap.add_argument("batches", type=int, nargs="*", help="batch ids")
    ap.add_argument("--list", action="store_true", help="list the recorded batches")
    args = ap.parse_args(argv)
    from claims_pipeline.benchmark.views import batches
    from claims_pipeline.store.db import sessions as open_sessions

    sessions = open_sessions(Settings().database_url)
    if args.list or not args.batches:
        with sessions() as s:
            for b in batches(s):
                print(f"{b['name']:>4}  {b['run_at'][:16]}  {b['folder']:<28} {b['correct']}/{b['scored']}  {b['title']}")
        return 0
    for batch_id in args.batches:
        r = replay_batch(sessions, batch_id)
        b, n = r["batch"], len(r["rows"])
        print(f"\n# batch {batch_id}: {b['title']} ({b['folder']})")
        print(f"right (decision and payout): {r['right']['before']}/{n} -> {r['right']['after']}/{n};  "
              f"decisions right: {r['decisions_right']['before']}/{n} -> {r['decisions_right']['after']}/{n}")
        for row in r["rows"]:
            if row["changed"]:
                bef, aft = row["before"], row["after"]
                mark = "+" if aft["match"] and not bef["match"] else "-" if bef["match"] and not aft["match"] else " "
                print(f"  {mark} {row['claim_id']:<42} expected {row['expected'] or '-':<9} "
                      f"{bef['actual']} {bef['actual_payout'] or ''} -> {aft['actual']} {aft['actual_payout'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
