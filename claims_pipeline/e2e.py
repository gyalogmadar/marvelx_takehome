"""The end-to-end strategy: one model call, the whole context, a decision.

This is the comparison baseline for the decomposed pipeline.  The model gets
the policy, the task framing, the claim description and every document
(images as images) in a single prompt and returns the decision, payout,
confidence and reasoning as structured output.  No facts, no checks, no
rules: whatever the model says is the result.  It fills the same result tree
so the API, UI and benchmark treat both strategies alike.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from claims_pipeline.components import Policy
from claims_pipeline.components.sources import IMAGE_TYPES, TEXT_TYPES, description_lines, document_key, file_lines
from claims_pipeline.domain import ClaimResult, Decision, Reading, ModelCall, Outcome
from claims_pipeline.llm.anthropic_backend import image_block
from claims_pipeline.llm.base import BackendError
from claims_pipeline.llm.calllog import claim_scope
from claims_pipeline.llm.config import cost_usd
from claims_pipeline.llm.types import Usage
from claims_pipeline.prompts import prompt
from claims_pipeline.pipeline import ClaimInput

TASK = prompt("e2e").text  # claims_pipeline/prompts/e2e.md


class EvidenceOut(BaseModel):
    document: str = Field(description="filename, or 'description'")
    quote: str
    relevance: str


class DocOut(BaseModel):
    filename: str
    doc_type: str
    language: str


class E2EOut(BaseModel):
    outcome: Literal["APPROVE", "DENY", "UNCERTAIN"]
    payout: float | None = None
    currency: str = "EUR"
    confidence: float = Field(ge=0, le=1)
    summary: str
    reasoning: str
    evidence: list[EvidenceOut] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    documents: list[DocOut] = Field(default_factory=list)


def build_content(claim: ClaimInput) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    submitted = claim.submitted_at.date().isoformat() if claim.submitted_at else "not recorded"
    meta = "\n".join(f"{k}: {v}" for k, v in claim.metadata.items())
    content.append({"type": "text", "text": f"# Claim description\n{claim.description}\n\n# Submission\nsubmitted: {submitted}\n{meta}".rstrip()})
    for f in claim.files:
        ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
        if ext in IMAGE_TYPES:
            content.append({"type": "text", "text": f"# Document: {f.filename} (image)"})
            content.append(image_block(f.content, IMAGE_TYPES[ext]))
            continue
        try:
            text = f.content.decode("utf-8")
        except UnicodeDecodeError:
            content.append({"type": "text", "text": f"# Document: {f.filename}\n(binary file, could not be read)"})
            continue
        numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate([l for l in text.splitlines() if l.strip()], start=1))
        content.append({"type": "text", "text": f"# Document: {f.filename}\n{numbered}"})
    return content


def _readings(claim: ClaimInput, out: E2EOut) -> list[Reading]:
    classified = {d.filename: d for d in out.documents}
    docs = [Reading(id="description", filename="description", kind="description", doc_type="claim_description",
                     lines=description_lines(claim.description))]
    for n, f in enumerate(claim.files, start=1):
        ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
        cls = classified.get(f.filename)
        doc_id = document_key(n, f.filename)
        if ext in IMAGE_TYPES:
            docs.append(Reading(id=doc_id, filename=f.filename, kind="image", media_type=IMAGE_TYPES[ext],
                                 doc_type=cls.doc_type if cls else "other", language=cls.language if cls else "en"))
            continue
        lines = file_lines(f.filename, f.content)
        if lines is None:
            docs.append(Reading(id=doc_id, filename=f.filename, kind="text", doc_type="unreadable"))
            continue
        docs.append(Reading(id=doc_id, filename=f.filename, kind=TEXT_TYPES.get(ext, "text"), lines=lines,
                            doc_type=cls.doc_type if cls else "other", language=cls.language if cls else "en"))
    return docs


def process_claim_e2e(claim: ClaimInput, policy: Policy, api) -> ClaimResult:
    system = prompt("e2e_system").render(role=prompt("role").render(), submission_stamp=prompt("submission_stamp").render(),
                                         policy=policy.text, task=prompt("e2e").render())
    t0 = time.perf_counter()
    try:
        with claim_scope(claim.claim_id, "e2e"):
            out, usage = api.parse("e2e", system, build_content(claim), E2EOut)
    except BackendError as exc:
        return _failed(claim, policy, exc, int((time.perf_counter() - t0) * 1000))
    latency = int((time.perf_counter() - t0) * 1000)
    evidence = "\n".join(f"- {e.document}: “{e.quote}” ({e.relevance})" for e in out.evidence)
    explanation = out.reasoning.strip() + (f"\n\nKey evidence:\n{evidence}" if evidence else "")
    decision = Decision(
        outcome=Outcome(out.outcome), confidence=out.confidence, summary=out.summary.strip(),
        payout=round(out.payout, 2) if out.outcome == "APPROVE" and out.payout is not None else None,
        currency=out.currency or policy.currency,
        unknowns=list(out.concerns) if out.outcome == "UNCERTAIN" else [],
        explanation=explanation,
    )
    if out.concerns and out.outcome != "UNCERTAIN":
        decision.explanation += "\n\nConcerns:\n" + "\n".join(f"- {c}" for c in out.concerns)
    return ClaimResult(
        claim_id=claim.claim_id, policy=policy.name, strategy="e2e", description=claim.description,
        submitted_at=claim.submitted_at, processed_at=datetime.now(timezone.utc),
        readings=_readings(claim, out), facts=[], exclusions=[], coverages=[], decision=decision,
        calls=[ModelCall(component="e2e", model=usage.model, call_id=usage.call_id,
                         reasoning_tokens=usage.reasoning_tokens,
                         input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                         cache_read_tokens=usage.cache_read_tokens, latency_ms=latency,
                         cost_usd=cost_usd(usage.model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens))],
    )


def _failed(claim: ClaimInput, policy: Policy, exc: BackendError, latency: int) -> ClaimResult:
    """The single call failed: refer the claim, with the failure as the open point."""
    usage = exc.usage or Usage(model="unknown")
    return ClaimResult(
        claim_id=claim.claim_id, policy=policy.name, strategy="e2e", description=claim.description,
        submitted_at=claim.submitted_at, processed_at=datetime.now(timezone.utc),
        readings=[], facts=[], exclusions=[], coverages=[],
        decision=Decision(outcome=Outcome.UNCERTAIN, confidence=0.0, currency=policy.currency,
                          summary="Referred for review. The model call failed.",
                          unknowns=[f"1 model call(s) failed, a rerun may resolve them: e2e: {exc}"]),
        calls=[ModelCall(component="e2e", model=usage.model, call_id=usage.call_id, ok=False, error=str(exc),
                         reasoning_tokens=usage.reasoning_tokens, input_tokens=usage.input_tokens,
                         output_tokens=usage.output_tokens, cache_read_tokens=usage.cache_read_tokens,
                         latency_ms=latency,
                         cost_usd=cost_usd(usage.model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens))],
    )
