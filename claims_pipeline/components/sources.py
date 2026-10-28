"""Evidence sources: turn the submitted files into Documents.

The customer's description and the submission metadata are documents too,
so facts taken from them carry provenance like everything else.  Every file
is passed to the reader component: images are transcribed, text files are
classified (their own lines are kept).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from claims_pipeline.domain import Document

from .policy import Policy

if TYPE_CHECKING:  # pragma: no cover
    from claims_pipeline.llm.base import ModelBackend
    from claims_pipeline.pipeline import ClaimInput

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
TEXT_TYPES = {".md": "markdown", ".markdown": "markdown", ".txt": "text"}


def _slug(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "file"


def load_documents(claim: "ClaimInput", policy: Policy, backend: "ModelBackend") -> list[Document]:
    from claims_pipeline.llm.types import DocumentReadRequest

    docs: list[Document] = [
        Document(id="description", filename="description", kind="description", doc_type="claim_description",
                 lines=[ln for ln in claim.description.splitlines() if ln.strip()] or [claim.description]),
    ]
    meta_lines = [f"submitted_at: {claim.submitted_at.date().isoformat()}"] if claim.submitted_at else []
    meta_lines += [f"{k}: {v}" for k, v in claim.metadata.items()]
    docs.append(Document(id="metadata", filename="metadata", kind="metadata", doc_type="claim_metadata", lines=meta_lines))

    for n, f in enumerate(claim.files, start=1):
        ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
        doc_id = f"d{n}-{_slug(f.filename)}"
        if ext in IMAGE_TYPES:
            req = DocumentReadRequest(document_id=doc_id, filename=f.filename, kind="image", media_type=IMAGE_TYPES[ext],
                                      image=f.content, doc_types=policy.doc_types, policy_name=policy.name)
            reading = backend.read_document(req)
            docs.append(Document(id=doc_id, filename=f.filename, kind="image", media_type=IMAGE_TYPES[ext],
                                 doc_type=reading.doc_type, language=reading.language, issuer=reading.issuer,
                                 lines=reading.lines or [], alteration_signs=reading.alteration_signs, summary=reading.summary))
            continue
        try:
            text = f.content.decode("utf-8")
        except UnicodeDecodeError:
            docs.append(Document(id=doc_id, filename=f.filename, kind="text", doc_type="unreadable",
                                 summary="binary or unsupported file format"))
            continue
        kind = TEXT_TYPES.get(ext, "text")
        file_lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        req = DocumentReadRequest(document_id=doc_id, filename=f.filename, kind=kind, text=text,
                                  doc_types=policy.doc_types, policy_name=policy.name)
        reading = backend.read_document(req)
        docs.append(Document(id=doc_id, filename=f.filename, kind=kind, doc_type=reading.doc_type,
                             language=reading.language, issuer=reading.issuer, lines=reading.lines or file_lines,
                             alteration_signs=reading.alteration_signs, summary=reading.summary))
    return docs
