"""How readings and facts are written into a model's input: numbered lines, values with their
confidence, and the scorer's probabilities when one ran.  Every component that shows
evidence to a model (the extractor, the judge, Clef) renders it here, the same way."""
from __future__ import annotations

from claims_pipeline.domain import Fact, Reading


def render_readings(documents: list[Reading]) -> str:
    """Documents as numbered lines, the form every later component reads them in.

    What the reader saw on a document (its alteration signs) and the scorer's
    probabilities for it, when one ran, are shown; the trust level and trust
    notes are not.  Those are code's conclusions from comparing facts, and a
    judge shown them answers "inconsistent" by reading them back."""
    parts = []
    for d in documents:
        head = f"### {d.filename}  [id={d.id}, type={d.doc_type}, kind={d.kind}, language={d.language}]"
        if d.issuer:
            head += f"\nissuer: {d.issuer}"
        if d.alteration_signs:
            head += "\nalteration signs noticed by the reader: " + "; ".join(d.alteration_signs)
        if d.scores:
            head += "\nindependent scorer: " + "; ".join(
                f"{'type' if q == 'doc_type' else q} {probabilities(p)}" for q, p in d.scores.items())
        body = "\n".join(f"{i}: {line}" for i, line in enumerate(d.lines, start=1)) or "(no readable content)"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def probabilities(p: dict[str, float], top: int = 3) -> str:
    """``yes 0.82, no 0.10, unknown 0.08``: the most likely answers first."""
    return ", ".join(f"{k} {v:.2f}" for k, v in sorted(p.items(), key=lambda kv: -kv[1])[:top])


def render_facts(facts: list[Fact]) -> str:
    rows = []
    for f in facts:
        if f.value is None:
            rows.append(f"- {f.name}: unknown")
        else:
            scored = f"; independent scorer: {probabilities(f.scores)}" if f.scores else ""
            rows.append(f"- {f.name}: {f.value} (confidence {f.confidence:.2f}" + (f", {f.note}" if f.note else "")
                        + scored + ")")
    return "\n".join(rows) or "(none)"
