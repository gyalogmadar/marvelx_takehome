"""Run the pipeline over a folder of claims and score it.

    python -m claims_pipeline.benchmark.run mockclaims --out results/mock
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --backend openai --out results/decomposed
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --backend openai --strategy e2e --out results/e2e
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --strategy clef --out results/clef
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --backend openai --judge clef --out results/decomposed-clef
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --backend anthropic --scorer clef --out results/claude-scored
    python -m claims_pipeline.benchmark.compare results/decomposed results/e2e

Each claim folder gets a <claim>.json result; the folder gets results.json and
results.md.  With the scripted backend every claim folder needs an oracle.json.
The e2e strategy (one model call per claim) always needs the real model; the
clef strategy (one Clef request per claim) needs only CLAIMS_CLEF_BASE_URL.
``--judge clef`` keeps the decomposed pipeline but has Clef answer every judged
check; with ``--backend scripted`` that judges the mock claims with Clef alone.
``--scorer clef`` keeps every component on the main backend and has Clef score
each document reading and extracted fact; the judge reads the scores and they
become the confidences.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from claims_pipeline.benchmark.loader import claim_dirs, load_claim_dir
from claims_pipeline.benchmark.report import markdown, metrics, score_row
from claims_pipeline.domain import ClaimResult
from claims_pipeline.policies import get_policy
from claims_pipeline.settings import Settings
from claims_pipeline.store import models
from claims_pipeline.store import repository as repo
from claims_pipeline.store.db import sessions as open_sessions
from claims_pipeline.store.runs import current_config, finish_run, prepare_run
from claims_pipeline.strategies import strategy as run_strategy


def run(claims_root: Path, out: Path, policy_name: str | None, backend_kind: str, explain: bool, title: str,
        strategy: str = "decomposed", judge: str | None = None, scorer: str | None = None, store: bool = True) -> dict:
    settings = Settings().model_copy(update={"trace_dir": str(out / "trace"), "backend": backend_kind})
    if judge is not None:
        settings = settings.model_copy(update={"judge_backend": judge})
    if scorer is not None:
        settings = settings.model_copy(update={"scorer": scorer})
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    results: dict[str, ClaimResult] = {}
    if strategy == "e2e" and backend_kind == "scripted":
        raise ValueError("the e2e strategy needs a real model: --backend anthropic|openai")
    sessions = batch = bench = None
    if store:
        sessions = open_sessions(settings.database_url)
        with sessions() as s, s.begin():
            bench_row = repo.benchmark(s, Path(claims_root).resolve().name)
            batch = repo.start_batch(s, out.name, title, current_config(s, settings, strategy=strategy,
                                                                       backend=backend_kind, explain=explain), bench_row)
            bench = bench_row.id
    for claim_dir in claim_dirs(claims_root):
        loaded = load_claim_dir(claim_dir)
        policy = get_policy(loaded.policy or policy_name or settings.default_policy)
        expected = loaded.expected or {}
        row = {"claim_id": loaded.input.claim_id, "policy": policy.name, "note": loaded.note,
               "expected": expected.get("decision"), "expected_payout": expected.get("payout"),
               "actual": None, "actual_payout": None, "match": False, "confidence": None, "summary": None}
        def process() -> ClaimResult:
            oracle = None
            if backend_kind == "scripted" and strategy == "decomposed":
                if loaded.oracle_path is None:
                    raise FileNotFoundError("scripted backend needs an oracle.json in the claim folder")
                oracle = json.loads(loaded.oracle_path.read_text())
            return run_strategy(settings, strategy, oracle=oracle, explain=explain)(loaded.input, policy)

        try:
            if sessions is None:
                result = process()
            else:  # recorded: the claim, the run, every model call, the result tree (claims_pipeline/store)
                run_id = prepare_run(sessions, loaded.input, policy, settings, strategy=strategy, backend=backend_kind,
                                     explain=explain, batch_id=batch, origin="benchmark")
                with sessions() as s, s.begin():
                    repo.set_case(s, s.get(models.Benchmark, bench), claim_dir.name, s.get(models.Run, run_id).claim,
                                  expected.get("decision"), expected.get("payout"), loaded.note)
                result = finish_run(sessions, run_id, process)
                row["run_id"] = run_id
        except Exception as exc:  # one bad claim must not stop the run
            row["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc(file=sys.stderr)
            rows.append(row)
            continue
        results[result.claim_id] = result
        (out / f"{result.claim_id}.json").write_text(result.model_dump_json(indent=2))
        score_row(row, result)
        rows.append(row)
        flag = "ok " if row["match"] else ("-- " if not row["expected"] else "MISS")
        print(f"{flag} {row['claim_id']:<45} expected {str(row['expected']):<10} got {row['actual']:<10} payout {row['actual_payout']}"
              + (f"  ({row['failed_calls']} failed model calls)" if row["failed_calls"] else ""))

    m = metrics(rows, results)
    (out / "results.json").write_text(json.dumps({
        "title": title, "name": out.name, "backend": backend_kind, "strategy": strategy,
        "judge": settings.judge_backend or None, "scorer": settings.scorer or None, "batch_id": batch,
        "claims_root": str(Path(claims_root).resolve()),
        "run_at": datetime.now(timezone.utc).isoformat(), "metrics": m, "claims": rows}, indent=2))
    (out / "results.md").write_text(markdown(rows, results, m, title))
    acc = f"{m['accuracy']:.1%}" if m["accuracy"] is not None else "n/a"
    if (out / "trace" / "calls.jsonl").exists():
        print(f"model calls logged in full: {out / 'trace' / 'calls.jsonl'}")
    print(f"\n{m['correct']}/{m['scored']} correct ({acc}); payouts exact {m['payout']['exact']}/{m['payout']['scored']}; "
          f"errors {m['unprocessed']}; report: {out / 'results.md'}")
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("claims", type=Path)
    ap.add_argument("--out", type=Path, default=Path("results/latest"))
    ap.add_argument("--policy", default=None, help="policy for folders whose claim.json names none")
    ap.add_argument("--backend", default=None, choices=["scripted", "anthropic", "openai"],
                    help="default: CLAIMS_BACKEND or scripted")
    ap.add_argument("--strategy", default="decomposed", choices=["decomposed", "e2e", "clef"],
                    help="decomposed: components + rules (default); e2e: one model call with the whole context; "
                         "clef: one Clef decision-model request with the whole context")
    ap.add_argument("--judge", default=None, choices=["clef"],
                    help="decomposed only: answer judged checks with Clef (default: CLAIMS_JUDGE_BACKEND)")
    ap.add_argument("--scorer", default=None, choices=["clef"],
                    help="decomposed only: Clef scores each document reading and extracted fact; the judge "
                         "reads the scores (default: CLAIMS_SCORER)")
    ap.add_argument("--no-store", action="store_true",
                    help="do not record the runs in the database (CLAIMS_DATABASE_URL); files only")
    ap.add_argument("--no-explain", action="store_true", help="skip the narrative step")
    ap.add_argument("--title", default=None)
    args = ap.parse_args(argv)
    backend = "clef" if args.strategy == "clef" else (args.backend or Settings().backend)
    judge = args.judge or Settings().judge_backend
    scorer = args.scorer or Settings().scorer
    label = backend + (f", {judge} judge" if judge and args.strategy == "decomposed" else "") + (
        f", {scorer} scores" if scorer and args.strategy == "decomposed" else "")
    title = args.title or f"Benchmark ({args.strategy}, {label})"
    m = run(args.claims, args.out, args.policy, backend, not args.no_explain, title, args.strategy, args.judge, args.scorer,
            store=not args.no_store)
    return 0 if m["unprocessed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
