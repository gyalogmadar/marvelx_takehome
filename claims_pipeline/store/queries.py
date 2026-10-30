"""What the API reads, in the shapes the UI already knows.

    claim_record(s, name, run_id=None)   a claim with one of its runs (the latest by default)
    claim_summaries(s)                   submitted claims, newest first, with their latest decision
    runs_of(s, name)                     every run of a claim: when, how, what it decided
    call_records(s, run_id)              a run's model calls in full
    document(s, run_id, key)             the file behind a reading

Benchmark executions are read in benchmark/views.py, which scores them.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .repository import load_run


def latest_claim(s: Session, name: str) -> m.Claim | None:
    return s.scalar(select(m.Claim).where(m.Claim.name == name).order_by(m.Claim.created_at.desc()))


def _run(s: Session, claim: m.Claim, run_id: str | None) -> m.Run | None:
    if run_id:
        run = s.get(m.Run, run_id)
        return run if run is not None and run.claim_id == claim.id else None
    return claim.runs[-1] if claim.runs else None


def claim_with_run(s: Session, claim: m.Claim, run: m.Run | None) -> dict[str, Any]:
    return {
        "claim_id": claim.name, "policy": claim.policy, "status": run.status if run else "processing",
        "description": claim.description, "metadata": claim.meta,
        "submitted_at": claim.submitted_at.isoformat() if claim.submitted_at else None,
        "files": [d.filename for d in claim.documents],
        "result": load_run(s, run.id).model_dump(mode="json") if run is not None and run.status == "done" else None,
        "error": run.error if run else None, "run_id": run.id if run else None,
        "created_at": claim.created_at.isoformat(),
        "updated_at": (run.finished_at or run.started_at).isoformat() if run else claim.created_at.isoformat(),
    }


def claim_record(s: Session, name: str, run_id: str | None = None) -> dict[str, Any] | None:
    claim = latest_claim(s, name)
    if claim is None:
        return None
    run = _run(s, claim, run_id)
    if run_id and run is None:
        return None
    return claim_with_run(s, claim, run)


def claim_summaries(s: Session) -> list[dict[str, Any]]:
    claims = s.scalars(select(m.Claim).where(m.Claim.origin == "submitted").order_by(m.Claim.created_at.desc()))
    out = []
    for claim in claims:
        run = claim.runs[-1] if claim.runs else None
        decision = run.decision if run else None
        out.append({
            "claim_id": claim.name, "policy": claim.policy, "status": run.status if run else "processing",
            "strategy": run.strategy if run else None,
            "submitted_at": claim.submitted_at.isoformat() if claim.submitted_at else None,
            "created_at": claim.created_at.isoformat(), "files": [d.filename for d in claim.documents],
            "decision": decision.outcome if decision else None, "confidence": decision.confidence if decision else None,
            "payout": decision.payout if decision else None, "currency": decision.currency if decision else None,
            "summary": decision.summary if decision else None,
            "coverages_applied": decision.coverages_applied if decision else [],
            "error": run.error if run else None, "description": claim.description[:160],
        })
    return out


def runs_of(s: Session, name: str) -> list[dict[str, Any]] | None:
    claim = latest_claim(s, name)
    if claim is None:
        return None
    return [{"run_id": r.id, "status": r.status, "strategy": r.strategy, "config_id": r.config_id,
             "policy_id": r.policy_id, "batch_id": r.batch_id, "started_at": r.started_at.isoformat(),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "outcome": r.decision.outcome if r.decision else None, "error": r.error} for r in claim.runs]


def call_records(s: Session, run_id: str, component: str | None = None) -> list[dict[str, Any]]:
    run = s.get(m.Run, run_id)
    calls = [c for c in (run.calls if run else []) if component is None or c.component == component]
    return [{
        "call_id": c.call_id, "ts": c.created_at.isoformat() if c.created_at else None, "run_id": c.run_id,
        "component": c.component, "provider": c.provider, "endpoint": c.endpoint, "model": c.model,
        "prompts": c.prompts, "request": c.request, "response": c.response, "output_text": c.output_text,
        "reasoning": c.reasoning, "parsed": c.parsed,
        "usage": {"input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                  "cache_read_tokens": c.cache_read_tokens, "reasoning_tokens": c.reasoning_tokens},
        "latency_ms": c.latency_ms, "cost_usd": c.cost_usd, "error": c.error,
    } for c in calls]


def document(s: Session, run_id: str, key: str) -> m.Document | None:
    reading = s.scalar(select(m.Reading).where(m.Reading.run_id == run_id, m.Reading.key == key))
    return reading.document if reading is not None else None
