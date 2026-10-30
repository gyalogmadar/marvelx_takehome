"""Explanation.

The decision is already made and summarised in code.  The model's only job
here is to narrate the check tree for a human reader.  It cannot change the
outcome, and when no model is available the code summary stands on its own.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from claims_pipeline.domain import ClaimResult, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from claims_pipeline.llm.base import ModelBackend


def narrate(result: ClaimResult, backend: "ModelBackend") -> str | None:
    from claims_pipeline.llm.base import BackendError
    from claims_pipeline.llm.types import ExplainRequest

    try:
        return backend.explain(ExplainRequest(result=result))
    except BackendError:
        return None  # the call is traced as failed; the decision summary stands


def evidence_outline(result: ClaimResult) -> list[str]:
    """Indented text rendering of the evidence tree, used by reports and tests."""
    lines = [f"{result.decision.outcome.value} ({result.decision.confidence:.2f}): {result.decision.summary}"]
    by_doc = {d.id: d for d in result.readings}
    facts = {f.name: f for f in result.facts}

    def check_lines(r, indent: str) -> None:
        marker = {Verdict.PASS: "+", Verdict.FAIL: "x", Verdict.UNKNOWN: "?"}[r.verdict]
        lines.append(f"{indent}[{marker}] {r.id}: {r.rationale}")
        if r.quote:
            lines.append(f"{indent}    \"{r.quote}\"")
        for name in r.facts_used:
            f = facts.get(name)
            if not f:
                continue
            lines.append(f"{indent}    {name} = {f.value} (conf {f.confidence:.2f})")
            for o in f.observations:
                d = by_doc.get(o.document_id)
                where = f"{d.filename}" if d else o.document_id
                where += f" line {o.line}" if o.line else ""
                lines.append(f"{indent}        <- {where}: \"{o.quote}\"")

    lines.append("  exclusions")
    for e in result.exclusions:
        check_lines(e, "    ")
    if result.red_flags:
        lines.append("  red flags")
        for r in result.red_flags:
            check_lines(r, "    ")
    for c in result.coverages:
        lines.append(f"  coverage {c.name}: {c.status}" + (f", payout {c.payout}" if c.payout is not None else ""))
        check_lines(c.match, "    ")
        check_lines(c.proofs, "    ")
        for k in c.checks:
            check_lines(k, "    ")
    return lines
