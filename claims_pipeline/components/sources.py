"""Evidence sources: turn the submitted files into Documents.

The customer's description and the submission metadata are documents too,
so facts taken from them carry provenance like everything else.  Every file
is passed to the reader component: images are transcribed, text files are
classified (their own lines are kept).  A failed read leaves the document
``unreadable`` with the error as its summary, so presence checks go UNKNOWN
instead of failing; a text file keeps its lines for the extractor.  With a
scorer routed in, each reading is scored as soon as it is made (scoring.py).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from claims_pipeline.domain import Reading

from .policy import Policy
from .scoring import scores_for_reading

if TYPE_CHECKING:  # pragma: no cover
    from claims_pipeline.llm.base import ModelBackend
    from claims_pipeline.pipeline import ClaimInput

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
TEXT_TYPES = {".md": "markdown", ".markdown": "markdown", ".txt": "text"}


def _slug(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "file"


# How a claim's inputs become lines.  Every strategy uses these, and the store uses them to
# rebuild a reading's lines from the document instead of keeping a copy.
def document_key(n: int, filename: str) -> str:
    """How a run refers to the n-th file (from 1): ``d1-booking-confirmation``."""
    return f"d{n}-{_slug(filename)}"


def description_lines(description: str) -> list[str]:
    return [ln for ln in description.splitlines() if ln.strip()] or [description]


def metadata_lines(submitted_at: datetime | None, metadata: dict[str, str]) -> list[str]:
    lines = [f"submitted_at: {submitted_at.date().isoformat()}"] if submitted_at else []
    return lines + [f"{k}: {v}" for k, v in metadata.items()]


def file_lines(filename: str, content: bytes) -> list[str] | None:
    """A text file's own lines; None for an image or a file that is not UTF-8 text."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext in IMAGE_TYPES:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def read_documents(claim: "ClaimInput", policy: Policy, backend: "ModelBackend") -> list[Reading]:
    from claims_pipeline.llm.base import BackendError, call_id_of
    from claims_pipeline.llm.types import DocumentReading, DocumentReadRequest

    def read(req: DocumentReadRequest, **reading) -> Reading:
        """Read one document (and score the reading, when a scorer is routed in)."""
        try:
            out = backend.read_document(req)
            call_id = call_id_of(backend)
        except BackendError as exc:
            out = DocumentReading(doc_type="unreadable", summary=f"not read, the model call failed: {exc}")
            call_id = call_id_of(exc)
        scores, score_call_id = scores_for_reading(backend, req, out)
        lines = out.lines or reading.pop("lines", [])
        return Reading(id=req.document_id, filename=req.filename, kind=req.kind, doc_type=out.doc_type,
                       language=out.language, issuer=out.issuer, lines=lines, alteration_signs=out.alteration_signs,
                       summary=out.summary, review=out.review, scores=scores, call_id=call_id, score_call_id=score_call_id, **reading)

    readings: list[Reading] = [
        Reading(id="description", filename="description", kind="description", doc_type="claim_description",
                lines=description_lines(claim.description)),
        Reading(id="metadata", filename="metadata", kind="metadata", doc_type="claim_metadata",
                lines=metadata_lines(claim.submitted_at, claim.metadata)),
    ]

    for n, f in enumerate(claim.files, start=1):
        ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
        doc_id = document_key(n, f.filename)
        if ext in IMAGE_TYPES:
            req = DocumentReadRequest(document_id=doc_id, filename=f.filename, kind="image", media_type=IMAGE_TYPES[ext],
                                      image=f.content, doc_types=policy.doc_types, policy_name=policy.name)
            readings.append(read(req, media_type=IMAGE_TYPES[ext]))
            continue
        try:
            text = f.content.decode("utf-8")
        except UnicodeDecodeError:
            readings.append(Reading(id=doc_id, filename=f.filename, kind="text", doc_type="unreadable",
                                    summary="binary or unsupported file format"))
            continue
        kind = TEXT_TYPES.get(ext, "text")
        req = DocumentReadRequest(document_id=doc_id, filename=f.filename, kind=kind, text=text,
                                  doc_types=policy.doc_types, policy_name=policy.name)
        # a text file keeps its own lines unless the reader transcribed it
        readings.append(read(req, lines=file_lines(f.filename, f.content)))
    return readings
