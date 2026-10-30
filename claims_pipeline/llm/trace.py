"""Tracing: every model call is recorded with component, model, tokens, cache
hits, latency and cost.  The records travel with the claim result, feed the
benchmark's per-component cost table, and are the raw material for later
prompt tuning or fine-tuning of individual components."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from claims_pipeline.domain import ModelCall

from .base import SCORERS, ModelBackend
from .pricing import cost_usd
from .types import (
    DocumentReading,
    DocumentReadRequest,
    ExplainRequest,
    ExtractedObservation,
    ExtractRequest,
    JudgeRequest,
    Judgment,
    Usage,
)


@dataclass
class Tracer:
    calls: list[ModelCall] = field(default_factory=list)

    def record(self, component: str, usage: Usage | None, latency_ms: int, error: str | None = None) -> None:
        usage = usage or Usage(model="unknown")
        self.calls.append(ModelCall(
            component=component, model=usage.model, call_id=usage.call_id,
            reasoning_tokens=usage.reasoning_tokens, input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens, cache_read_tokens=usage.cache_read_tokens,
            latency_ms=latency_ms, ok=error is None, error=error,
            cost_usd=cost_usd(usage.model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens),
        ))


class TracedBackend:
    """Wraps any backend and records each call on a Tracer."""

    def __init__(self, inner: ModelBackend, tracer: Tracer):
        self.inner = inner
        self.tracer = tracer
        self.name = inner.name
        self.last_usage: Usage | None = None
        for component in SCORERS:  # traced only when the inner backend has them
            if hasattr(inner, component):
                setattr(self, component, lambda req, c=component: self._call(c, getattr(self.inner, c), req))

    def _call(self, component: str, fn, req):
        t0 = time.perf_counter()
        try:
            result = fn(req)
        except Exception as exc:  # record, then re-raise: the components decide what a failure means
            # the failed call's own usage (the client attaches it), never the previous call's last_usage
            self.last_usage = getattr(exc, "usage", None)
            self.tracer.record(component, self.last_usage, int((time.perf_counter() - t0) * 1000), str(exc))
            raise
        self.last_usage = getattr(self.inner, "last_usage", None)
        self.tracer.record(component, self.last_usage, int((time.perf_counter() - t0) * 1000))
        return result

    def read_document(self, req: DocumentReadRequest) -> DocumentReading:
        return self._call("read_document", self.inner.read_document, req)

    def extract_facts(self, req: ExtractRequest) -> list[ExtractedObservation]:
        return self._call("extract_facts", self.inner.extract_facts, req)

    def judge(self, req: JudgeRequest) -> Judgment:
        return self._call("judge", self.inner.judge, req)

    def explain(self, req: ExplainRequest) -> str | None:
        return self._call("explain", self.inner.explain, req)
