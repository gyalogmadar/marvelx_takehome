"""Run the pipeline over a folder of claims and score it.

    python -m claims_pipeline.benchmark.run mockclaims --out results/mock
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --backend openai --out results/decomposed
    python -m claims_pipeline.benchmark.run "path/to/claims" --policy cfsr --backend openai --strategy e2e --out results/e2e
    python -m claims_pipeline.benchmark.compare results/decomposed results/e2e

Each claim folder gets a <claim>.json result; the folder gets results.json and
results.md.  With the scripted backend every claim folder needs an oracle.json.
The e2e strategy (one model call per claim) always needs the real model.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from claims_pipeline.benchmark.loader import claim_dirs, load_claim_dir
from claims_pipeline.benchmark.report import markdown, metrics
from claims_pipeline.domain import ClaimResult
from claims_pipeline.llm import Settings
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy


def make_backend(kind: str, oracle_path: Path | None, settings: Settings):
    if kind == "scripted":
        if oracle_path is None:
            raise FileNotFoundError("scripted backend needs an oracle.json in the claim folder")
        return ScriptedBackend.from_file(oracle_path)
    if kind in ("anthropic", "openai"):
        from claims_pipeline.llm.clients import make_decomposed_backend

        return make_decomposed_backend(settings.model_copy(update={"backend": kind}))
    raise ValueError(f"unknown backend {kind!r}")


def run(claims_root: Path, out: Path, policy_name: str | None, backend_kind: str, explain: bool, title: str,
        strategy: str = "decomposed") -> dict:
    settings = Settings()
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    results: dict[str, ClaimResult] = {}
    e2e_api = None
    if strategy == "e2e":
        from claims_pipeline.e2e import process_claim_e2e
        from claims_pipeline.llm.clients import make_client

        if backend_kind == "scripted":
            raise ValueError("the e2e strategy needs a real model: --backend anthropic|openai")
        e2e_api = make_client(settings.model_copy(update={"backend": backend_kind}))
    for claim_dir in claim_dirs(claims_root):
        loaded = load_claim_dir(claim_dir)
        policy = get_policy(loaded.policy or policy_name or settings.default_policy)
        expected = loaded.expected or {}
        row = {"claim_id": loaded.input.claim_id, "policy": policy.name, "note": loaded.note,
               "expected": expected.get("decision"), "expected_payout": expected.get("payout"),
               "actual": None, "actual_payout": None, "match": False, "confidence": None, "summary": None}
        try:
            if strategy == "e2e":
                result = process_claim_e2e(loaded.input, policy, e2e_api)
            else:
                backend = make_backend(backend_kind, loaded.oracle_path, settings)
                result = process_claim(loaded.input, policy, backend, explain=explain)
        except Exception as exc:  # one bad claim must not stop the run
            row["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc(file=sys.stderr)
            rows.append(row)
            continue
        results[result.claim_id] = result
        (out / f"{result.claim_id}.json").write_text(result.model_dump_json(indent=2))
        row.update(actual=result.decision.outcome.value, actual_payout=result.decision.payout,
                   confidence=result.decision.confidence, summary=result.decision.summary)
        row["match"] = bool(row["expected"]) and row["expected"] == row["actual"] and (
            row["expected_payout"] is None or (row["actual_payout"] is not None and abs(row["actual_payout"] - row["expected_payout"]) < 0.01))
        rows.append(row)
        flag = "ok " if row["match"] else ("-- " if not row["expected"] else "MISS")
        print(f"{flag} {row['claim_id']:<45} expected {str(row['expected']):<10} got {row['actual']:<10} payout {row['actual_payout']}")

    m = metrics(rows, results)
    (out / "results.json").write_text(json.dumps({
        "title": title, "name": out.name, "backend": backend_kind, "strategy": strategy,
        "claims_root": str(Path(claims_root).resolve()),
        "run_at": datetime.now(timezone.utc).isoformat(), "metrics": m, "claims": rows}, indent=2))
    (out / "results.md").write_text(markdown(rows, results, m, title))
    acc = f"{m['accuracy']:.1%}" if m["accuracy"] is not None else "n/a"
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
    ap.add_argument("--strategy", default="decomposed", choices=["decomposed", "e2e"],
                    help="decomposed: components + rules (default); e2e: one model call with the whole context")
    ap.add_argument("--no-explain", action="store_true", help="skip the narrative step")
    ap.add_argument("--title", default=None)
    args = ap.parse_args(argv)
    backend = args.backend or Settings().backend
    title = args.title or f"Benchmark ({args.strategy}, {backend})"
    m = run(args.claims, args.out, args.policy, backend, not args.no_explain, title, args.strategy)
    return 0 if m["unprocessed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
