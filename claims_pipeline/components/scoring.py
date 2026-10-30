"""Scoring: an independent probability on what the reader and the extractor concluded.

A scorer (Clef, ``CLAIMS_SCORER=clef``) changes no conclusion.  It answers
the same questions the reader and the extractor answered, from the evidence
alone, and its probability for their answer is recorded on the document and
on the fact.  Those probabilities travel up the result tree:

- a fact's confidence becomes the scorer's "yes", so every mechanical check
  that uses the fact inherits it;
- a proof's confidence becomes the weakest scored document type it relies on;
- the judge sees both, next to the documents and facts it reads.

Without a scorer, or when its call fails, the components' own confidences
stand and nothing else changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from claims_pipeline.domain import Reading

from .facts import Facts

if TYPE_CHECKING:  # pragma: no cover
    from claims_pipeline.llm.base import ModelBackend
    from claims_pipeline.llm.types import DocumentReading, DocumentReadRequest, Scores


def scores_for_reading(backend: "ModelBackend", req: "DocumentReadRequest",
                       reading: "DocumentReading") -> tuple[dict[str, "Scores"], str | None]:
    """The scorer's probabilities for one reading and the id of the call, or ({}, None)."""
    from claims_pipeline.llm.base import BackendError, call_id_of
    from claims_pipeline.llm.types import DocumentScoreRequest

    if not hasattr(backend, "score_document") or reading.doc_type == "unreadable":
        return {}, None
    try:
        return backend.score_document(DocumentScoreRequest(read=req, reading=reading)), call_id_of(backend)
    except BackendError as exc:
        return {}, call_id_of(exc)


def score_facts(backend: "ModelBackend", facts: Facts, readings: list[Reading], description: str,
                policy_text: str) -> None:
    """Put the scorer's probabilities on every known fact read from the evidence.  A fact
    known only from the system's own metadata (the submission date) is not scored."""
    from claims_pipeline.llm.base import BackendError, call_id_of
    from claims_pipeline.llm.types import FactScoreRequest, FactToScore

    if not hasattr(backend, "score_facts"):
        return
    to_score = [f for f in facts.as_list()
                if f.known and any(o.document_id != "metadata" for o in f.observations)]
    if not to_score:
        return
    try:
        scores = backend.score_facts(FactScoreRequest(
            description=description, readings=readings, policy_text=policy_text,
            facts=[FactToScore(name=f.name, hint=facts.specs[f.name].hint, value=facts.display(f.name))
                   for f in to_score]))
    except BackendError:
        return
    call_id = call_id_of(backend)
    for f in to_score:
        if f.name in scores:
            f.scores, f.score_call_id = scores[f.name], call_id
            f.confidence = round(f.scores.get("yes", 0.0), 3)
