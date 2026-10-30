"""Evidence trust.

Assessed once, before the checks run, because an untrusted document poisons
every fact read from it.  Two signals: alteration signs the reader noticed on
the document itself, and facts whose observations disagree across documents.
"""
from __future__ import annotations

from claims_pipeline.domain import Reading, Trust

from .facts import Facts


def assess_trust(documents: list[Reading], facts: Facts) -> None:
    by_id = {d.id: d for d in documents}
    for doc in documents:
        if doc.kind in ("description", "metadata"):
            continue
        if len(doc.alteration_signs) >= 2:
            doc.trust = Trust.UNTRUSTED
            doc.trust_reasons.extend(f"alteration sign: {s}" for s in doc.alteration_signs)
        elif doc.alteration_signs:
            doc.trust = Trust.QUESTIONABLE
            doc.trust_reasons.append(f"alteration sign: {doc.alteration_signs[0]}")

    for fact in facts.as_list():
        if not (fact.note and fact.note.startswith("observations disagree")):
            continue
        involved = {o.document_id for o in fact.observations}
        for doc_id in involved:
            doc = by_id.get(doc_id)
            if doc is None or doc.kind in ("description", "metadata"):
                continue
            if doc.trust == Trust.TRUSTED:
                doc.trust = Trust.QUESTIONABLE
            doc.trust_reasons.append(f"{fact.name} disagrees with another document")
