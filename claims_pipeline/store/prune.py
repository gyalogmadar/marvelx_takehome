"""Remove a benchmark from the store: its claims, every run of them, every model call, then compact.

    python -m claims_pipeline.store.prune mockclaims --dry-run    # what would go
    python -m claims_pipeline.store.prune mockclaims              # remove it, then VACUUM

Runs are never edited, but a whole benchmark can be dropped (the mock set is regenerated
from scripts/make_mockclaims.py and can be rerun at will).  Policy, prompt and config
snapshots stay: they are small and other runs may share them.  Stop the API and any
benchmark run on the same database first.
"""
from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from claims_pipeline.settings import Settings

from . import models as m
from .db import sessions as open_sessions


def plan(s: Session, benchmark: str) -> dict[str, Any]:
    bench = s.scalar(select(m.Benchmark).where(m.Benchmark.name == benchmark))
    if bench is None:
        raise LookupError(f"no benchmark {benchmark!r}")
    batches = list(s.scalars(select(m.Batch.id).where(m.Batch.benchmark_id == bench.id)))
    claims = set(s.scalars(select(m.BenchmarkCase.claim_id).where(m.BenchmarkCase.benchmark_id == bench.id)))
    claims |= set(s.scalars(select(m.Run.claim_id).where(m.Run.batch_id.in_(batches))))
    claims = {c for c in claims if s.get(m.Claim, c).origin == "benchmark"}   # never a submitted claim
    runs = set(s.scalars(select(m.Run.id).where(m.Run.claim_id.in_(claims))))
    runs |= set(s.scalars(select(m.Run.id).where(m.Run.batch_id.in_(batches))))
    return {"benchmark": bench.id, "batches": batches, "claims": claims, "runs": runs}


def prune(s: Session, benchmark: str) -> dict[str, int]:
    p = plan(s, benchmark)
    runs, claims = p["runs"], p["claims"]
    checks = select(m.CheckResult.id).where(m.CheckResult.run_id.in_(runs))
    facts = select(m.Fact.id).where(m.Fact.run_id.in_(runs))
    counts = {}
    for name, stmt in [
        ("check_evidence", delete(m.CheckEvidence).where(m.CheckEvidence.check_result_id.in_(checks))),
        ("check_result", delete(m.CheckResult).where(m.CheckResult.run_id.in_(runs))),
        ("observation", delete(m.Observation).where(m.Observation.fact_id.in_(facts))),
        ("fact", delete(m.Fact).where(m.Fact.run_id.in_(runs))),
        ("reading", delete(m.Reading).where(m.Reading.run_id.in_(runs))),
        ("coverage_result", delete(m.CoverageResult).where(m.CoverageResult.run_id.in_(runs))),
        ("decision", delete(m.Decision).where(m.Decision.run_id.in_(runs))),
        ("model_call", delete(m.ModelCall).where(m.ModelCall.run_id.in_(runs))),
        ("run", delete(m.Run).where(m.Run.id.in_(runs))),
        ("batch", delete(m.Batch).where(m.Batch.id.in_(p["batches"]))),
        ("benchmark_case", delete(m.BenchmarkCase).where(m.BenchmarkCase.benchmark_id == p["benchmark"])),
        ("document", delete(m.Document).where(m.Document.claim_id.in_(claims))),
        ("claim", delete(m.Claim).where(m.Claim.id.in_(claims))),
        ("benchmark", delete(m.Benchmark).where(m.Benchmark.id == p["benchmark"])),
    ]:
        counts[name] = s.execute(stmt.execution_options(synchronize_session=False)).rowcount
    return counts


def compact(engine) -> None:
    """Give the freed space back to the file system (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.execute(text("VACUUM"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("benchmark", help="the benchmark's name (its claims folder, e.g. mockclaims)")
    ap.add_argument("--dry-run", action="store_true", help="count what would be removed, change nothing")
    args = ap.parse_args(argv)
    sessions = open_sessions(Settings().database_url)
    with sessions() as s:
        p = plan(s, args.benchmark)
        calls = s.scalar(select(func.count()).select_from(m.ModelCall).where(m.ModelCall.run_id.in_(p["runs"])))
        print(f"{args.benchmark}: {len(p['batches'])} batches, {len(p['runs'])} runs, {len(p['claims'])} claims, "
              f"{calls} model calls")
    if args.dry_run:
        return 0
    with sessions() as s, s.begin():
        counts = prune(s, args.benchmark)
    print("removed: " + ", ".join(f"{n} {k}" for k, n in counts.items() if n))
    compact(sessions.kw["bind"])
    print("compacted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
