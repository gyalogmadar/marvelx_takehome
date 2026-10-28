from __future__ import annotations

from typing import Protocol

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

COMPONENTS = ("read_document", "extract_facts", "judge", "explain")


class BackendError(RuntimeError):
    pass


class ModelBackend(Protocol):
    """The four model-backed components.  ``last_usage`` is read by the tracer after each call."""

    name: str
    last_usage: Usage | None

    def read_document(self, req: DocumentReadRequest) -> DocumentReading: ...

    def extract_facts(self, req: ExtractRequest) -> list[ExtractedObservation]: ...

    def judge(self, req: JudgeRequest) -> Judgment: ...

    def explain(self, req: ExplainRequest) -> str | None: ...
