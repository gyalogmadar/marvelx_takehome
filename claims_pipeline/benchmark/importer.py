"""Import benchmark runs written before the store existed: results/<run>/ folders.

    python -m claims_pipeline.benchmark.importer results/mock-decomposed results/e2e ...
    python -m claims_pipeline.benchmark.importer results/*            # every folder; done ones are skipped

Each folder becomes a batch: its claims (read from the claims folder the run was made
on, ``claims_root`` in results.json), one run per claim with the result tree, and every
model call from ``trace/calls.jsonl`` that its result names (a folder written to more than
once logs the earlier runs' calls too; those are left out).  What those runs were made with was not recorded
at the time, so their config records only what results.json says (backend, strategy,
judge, scorer) and that they were imported; the code and prompt versions are unknown.
A folder already imported is skipped.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.domain import ClaimResult
from claims_pipeline.settings import Settings
from claims_pipeline.policies import get_policy

from claims_pipeline.store import models as m
from claims_pipeline.store.calls import store_call
from claims_pipeline.store.db import sessions as open_sessions
from claims_pipeline.store.repository import (
    add_claim, benchmark, fail_run, save_run, set_case, snapshot_policy, start_batch, start_run,
)
from claims_pipeline.store.runconfig import save_config


def import_folder(sessions, folder: Path) -> str:
    folder = Path(folder)
    meta = json.loads((folder / "results.json").read_text())
    run_at = meta.get("run_at") or datetime.fromtimestamp((folder / "results.json").stat().st_mtime, timezone.utc).isoformat()
    config = {"imported": {"folder": folder.name, "run_at": run_at},
              "run": {"strategy": meta.get("strategy", "decomposed"), "backend": meta.get("backend"),
                      "judge": meta.get("judge"), "scorer": meta.get("scorer"), "explain": True}}
    claims_root = Path(meta.get("claims_root") or "")
    calls = defaultdict(list)
    log = folder / "trace" / "calls.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                calls[rec.get("claim_id")].append(rec)

    with sessions() as s, s.begin():
        config_id = save_config(s, config)
        if s.scalar(select(m.Batch).where(m.Batch.name == folder.name, m.Batch.config_id == config_id)):
            return f"{folder.name}: already imported"
        bench = benchmark(s, claims_root.name or folder.name)
        batch_id = start_batch(s, folder.name, meta.get("title") or folder.name, config_id, bench)
        s.get(m.Batch, batch_id).started_at = datetime.fromisoformat(run_at)

    imported, skipped = 0, []
    for row in meta["claims"]:
        name = row["claim_id"]
        if not (claims_root / name).is_dir():
            skipped.append(f"{name} (claim folder not found under {claims_root})")
            continue
        loaded = load_claim_dir(claims_root / name)
        policy = get_policy(row.get("policy") or loaded.policy)
        with sessions() as s, s.begin():
            claim = add_claim(s, loaded.input, policy.name, origin="benchmark")
            set_case(s, s.get(m.Benchmark, bench.id), name, claim, row.get("expected"), row.get("expected_payout"),
                     row.get("note"))
            run_id = start_run(s, claim.id, config["run"]["strategy"], config_id=config_id,
                               policy_id=snapshot_policy(s, policy), batch_id=batch_id)
        result_path = folder / f"{name}.json"
        result = ClaimResult.model_validate_json(result_path.read_text()) if result_path.exists() else None
        named = {c.call_id for c in result.calls} if result else set()
        for rec in calls.get(name, []):
            if rec["call_id"] in named:
                store_call(sessions, {**rec, "run_id": run_id})
        with sessions() as s, s.begin():
            if result is not None:
                save_run(s, result, run_id=run_id)
                imported += 1
            else:
                fail_run(s, run_id, row.get("error") or "no result was written")
    note = f"; skipped {len(skipped)}: {', '.join(skipped)}" if skipped else ""
    return f"{folder.name}: {imported} runs imported as batch {batch_id}{note}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", type=Path, nargs="+", help="results/<run> folders (with results.json)")
    args = ap.parse_args(argv)
    sessions = open_sessions(Settings().database_url)
    for folder in args.folders:
        if (folder / "results.json").exists():
            print(import_folder(sessions, folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
