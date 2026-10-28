"""A scripted backend: answers come from an oracle file instead of a model.

Used for the adversarial mock claims and the tests, and it is the same shape
a replayed trace takes.  The oracle is keyed by filename (documents), fact
name (observations) and check id (judgments):

    {
      "documents": {"hospital-letter.md": {"doc_type": "medical_report", "language": "en",
                                           "issuer": "...", "alteration_signs": [], "lines": [...]}},
      "facts": {"incident_date": [{"document": "hospital-letter.md", "line": 4,
                                   "quote": "Admitted on 12 March 2025", "value": "2025-03-12",
                                   "confidence": 0.95}]},
      "judgments": {"prior_knowledge": {"answer": "no", "document": "...", "line": 2,
                                        "quote": "...", "rationale": "...", "confidence": 0.9}},
      "explanation": "optional narrative"
    }

Documents and judgments are strict by default: a missing entry raises, which
is how a mock author finds out what the pipeline will ask.  Facts are lenient:
no entry simply means no observation.
"""
from __future__ import annotations

import json
from pathlib import Path

from claims_pipeline.domain import Document

from .base import BackendError
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

DESCRIPTION_ID = "description"
METADATA_ID = "metadata"


class OracleMissing(BackendError):
    pass


class ScriptedBackend:
    name = "scripted"

    def __init__(self, oracle: dict, strict: bool = True):
        self.oracle = oracle
        self.strict = strict
        self.last_usage: Usage | None = None

    @classmethod
    def from_file(cls, path: Path | str, strict: bool = True) -> "ScriptedBackend":
        return cls(json.loads(Path(path).read_text()), strict=strict)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _id_by_filename(documents: list[Document]) -> dict[str, str]:
        ids = {d.filename: d.id for d in documents}
        ids.setdefault("description", DESCRIPTION_ID)
        ids.setdefault("metadata", METADATA_ID)
        return ids

    # -- components --------------------------------------------------------
    def read_document(self, req: DocumentReadRequest) -> DocumentReading:
        self.last_usage = Usage(model="scripted")
        entry = self.oracle.get("documents", {}).get(req.filename)
        if entry is None:
            if self.strict:
                raise OracleMissing(f"oracle has no 'documents' entry for {req.filename!r}")
            return DocumentReading(doc_type="other")
        return DocumentReading(**entry)

    def extract_facts(self, req: ExtractRequest) -> list[ExtractedObservation]:
        self.last_usage = Usage(model="scripted")
        ids = self._id_by_filename(req.documents)
        wanted = {f.name for f in req.facts}
        out: list[ExtractedObservation] = []
        for name, observations in self.oracle.get("facts", {}).items():
            if name not in wanted:
                continue
            for o in observations:
                filename = o.get("document", "description")
                if filename not in ids:
                    raise OracleMissing(f"fact {name!r} cites unknown document {filename!r}")
                out.append(ExtractedObservation(
                    fact=name, document_id=ids[filename], line=o.get("line"), quote=o.get("quote", ""),
                    value=o.get("value"), confidence=o.get("confidence", 1.0),
                ))
        return out

    def judge(self, req: JudgeRequest) -> Judgment:
        self.last_usage = Usage(model="scripted")
        entry = self.oracle.get("judgments", {}).get(req.check_id)
        if entry is None:
            if self.strict:
                raise OracleMissing(f"oracle has no 'judgments' entry for check {req.check_id!r}")
            return Judgment(answer="unknown", rationale="no scripted answer", confidence=0.0)
        ids = self._id_by_filename(req.documents)
        filename = entry.get("document")
        if filename is not None and filename not in ids:
            raise OracleMissing(f"judgment {req.check_id!r} cites unknown document {filename!r}")
        return Judgment(
            answer=entry["answer"], quote=entry.get("quote", ""),
            document_id=ids.get(filename) if filename else None, line=entry.get("line"),
            rationale=entry.get("rationale", ""), confidence=entry.get("confidence", 1.0),
        )

    def explain(self, req: ExplainRequest) -> str | None:
        self.last_usage = Usage(model="scripted")
        return self.oracle.get("explanation")
