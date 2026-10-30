"""Model calls into the database, as they happen: the sink store/runs.py gives the call log."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from claims_pipeline.llm.pricing import cost_usd

from . import models as m


def store_call(sessions, rec: dict[str, Any]) -> None:
    """One call log record as a ``model_call`` row of its run.  The run's result links to
    it later by ``call_id`` (repository.save_run)."""
    usage = rec.get("usage") or {}
    tokens = {k: int(usage.get(k) or 0)
              for k in ("input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens")}
    with sessions() as s, s.begin():
        s.add(m.ModelCall(
            call_id=rec["call_id"], run_id=rec["run_id"], component=rec["component"], provider=rec.get("provider"),
            endpoint=rec.get("endpoint"), model=rec.get("model") or usage.get("model") or "unknown",
            prompts=rec.get("prompts") or [], request=rec.get("request"), response=rec.get("response"),
            output_text=rec.get("output_text"), reasoning=rec.get("reasoning"), parsed=rec.get("parsed"),
            latency_ms=int(rec.get("latency_ms") or 0), ok=rec.get("error") is None, error=rec.get("error"),
            cost_usd=cost_usd(rec.get("model") or "", tokens["input_tokens"], tokens["output_tokens"],
                              tokens["cache_read_tokens"]),
            created_at=datetime.fromisoformat(rec["ts"]), **tokens))
