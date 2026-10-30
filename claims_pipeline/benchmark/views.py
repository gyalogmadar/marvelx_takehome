"""Recorded benchmark executions (batches), read from the store and scored against their cases.

    batches(s)                  every execution, newest first, with its score
    batch_results(s, id)        one execution: rows, metrics (the shape results.json has)
    batch_claim(s, id, case)    one claim of one execution, with what it was expected to get
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from claims_pipeline.store import models as m
from claims_pipeline.store.queries import claim_with_run
from claims_pipeline.store.repository import load_run

from .report import metrics, score_row


def _cases(s: Session, batch: m.Batch) -> dict[str, m.BenchmarkCase]:
    """The batch's benchmark cases by the claim they were run on."""
    if batch.benchmark_id is None:
        return {}
    cases = s.scalars(select(m.BenchmarkCase).where(m.BenchmarkCase.benchmark_id == batch.benchmark_id))
    return {c.claim_id: c for c in cases}


def _rows(s: Session, batch: m.Batch) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = _cases(s, batch)
    rows, results = [], {}
    for run in sorted(batch.runs, key=lambda r: r.claim.name):
        case = cases.get(run.claim_id)
        row = {"claim_id": run.claim.name, "run_id": run.id, "policy": run.claim.policy,
               "note": case.note if case else None, "expected": case.expected_outcome if case else None,
               "expected_payout": case.expected_payout if case else None, "actual": None, "actual_payout": None,
               "match": False, "confidence": None, "summary": None}
        if run.status == "done":
            result = load_run(s, run.id)
            results[result.claim_id] = result
            score_row(row, result)
        else:
            row["error"] = run.error or run.status
        rows.append(row)
    return rows, results


def batches(s: Session) -> list[dict[str, Any]]:
    out = []
    for batch in s.scalars(select(m.Batch).order_by(m.Batch.started_at.desc())):
        rows, results = _rows(s, batch)
        met = metrics(rows, results)
        config = s.get(m.RunConfig, batch.config_id).config
        out.append({"name": str(batch.id), "folder": batch.name, "title": batch.title,
                    "backend": config.get("run", {}).get("backend"), "strategy": config.get("run", {}).get("strategy"),
                    "config_id": batch.config_id, "run_at": batch.started_at.isoformat(), "claims": met["claims"],
                    "scored": met["scored"], "correct": met["correct"], "accuracy": met["accuracy"],
                    "total_cost_usd": met["total_cost_usd"]})
    return out


def batch_results(s: Session, batch_id: int) -> dict[str, Any] | None:
    batch = s.get(m.Batch, batch_id)
    if batch is None:
        return None
    rows, results = _rows(s, batch)
    run = s.get(m.RunConfig, batch.config_id).config.get("run", {})
    return {"title": batch.title, "name": str(batch.id), "folder": batch.name, "backend": run.get("backend"),
            "strategy": run.get("strategy"), "judge": run.get("judge"), "scorer": run.get("scorer"),
            "config_id": batch.config_id, "benchmark": batch.benchmark_id and s.get(m.Benchmark, batch.benchmark_id).name,
            "run_at": batch.started_at.isoformat(), "metrics": metrics(rows, results), "claims": rows}


def batch_claim(s: Session, batch_id: int, name: str) -> dict[str, Any] | None:
    batch = s.get(m.Batch, batch_id)
    run = next((r for r in (batch.runs if batch else []) if r.claim.name == name), None)
    if run is None:
        return None
    rec = claim_with_run(s, run.claim, run)
    case = _cases(s, batch).get(run.claim_id)
    rec.update(expected=case.expected_outcome if case else None,
               expected_payout=case.expected_payout if case else None, note=case.note if case else None)
    return rec
