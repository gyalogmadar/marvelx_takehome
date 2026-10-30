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
# Optional: a backend has these only when a scorer is routed in (``CLAIMS_SCORER``).  A scorer
# never changes what the reader or the extractor concluded; it puts a probability on it.
SCORERS = ("score_document", "score_facts")


class BackendError(RuntimeError):
    """A model call failed: transport, server, truncation, refusal or a reply off the schema.

    The clients attach ``usage`` (model, tokens spent, and the ``call_id`` of the call's line in
    the call log), so a failed call is traced and costed like a successful one.  Components turn
    this error into UNKNOWN; anything else is a bug and propagates.
    """

    usage: "Usage | None" = None


def call_id_of(source) -> str | None:
    """The call log id of the call just made: a backend's ``last_usage``, or a failed call's error."""
    usage = getattr(source, "last_usage", None) or getattr(source, "usage", None)
    return getattr(usage, "call_id", None)


class ModelBackend(Protocol):
    """The four model-backed components.  ``last_usage`` is read by the tracer after each call."""

    name: str
    last_usage: Usage | None

    def read_document(self, req: DocumentReadRequest) -> DocumentReading: ...

    def extract_facts(self, req: ExtractRequest) -> list[ExtractedObservation]: ...

    def judge(self, req: JudgeRequest) -> Judgment: ...

    def explain(self, req: ExplainRequest) -> str | None: ...
