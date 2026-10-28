"""The pipeline: the same five steps for every policy.

    read documents -> extract facts -> assess trust -> run checks -> decide -> narrate

Nothing in here is specific to a policy; the Policy object supplies facts,
coverages and exclusions, the backend supplies reading and judgment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from claims_pipeline.components import CheckContext, Facts, Policy
from claims_pipeline.components.explain import narrate
from claims_pipeline.components.sources import load_documents
from claims_pipeline.components.trust import assess_trust
from claims_pipeline.domain import ClaimResult, Observation
from claims_pipeline.llm.base import ModelBackend
from claims_pipeline.llm.trace import TracedBackend, Tracer
from claims_pipeline.llm.types import ExtractRequest, FactRequest


@dataclass
class ClaimFile:
    filename: str
    content: bytes


@dataclass
class ClaimInput:
    claim_id: str
    description: str
    files: list[ClaimFile] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    submitted_at: datetime | None = None  # None for historical claims whose submission date is unknown


def process_claim(claim: ClaimInput, policy: Policy, backend: ModelBackend, explain: bool = True) -> ClaimResult:
    tracer = Tracer()
    traced = TracedBackend(backend, tracer)

    documents = load_documents(claim, policy, traced)

    extracted = traced.extract_facts(ExtractRequest(
        description=claim.description, documents=documents,
        facts=[FactRequest(name=f.name, type=f.type, hint=f.hint) for f in policy.facts],
        policy_text=policy.text,
    ))
    observations = [(o.fact, Observation(document_id=o.document_id, line=o.line, quote=o.quote,
                                         value=o.value, confidence=o.confidence)) for o in extracted]
    # The submission date is known to the system; record it with provenance unless the extractor found one.
    if (claim.submitted_at is not None and any(f.name == "claim_submitted" for f in policy.facts)
            and not any(n == "claim_submitted" for n, _ in observations)):
        observations.append(("claim_submitted", Observation(
            document_id="metadata", line=1, quote=f"submitted_at: {claim.submitted_at.date().isoformat()}",
            value=claim.submitted_at.date().isoformat(), confidence=1.0)))
    facts = Facts.build(policy.facts, observations, labels={d.id: d.filename for d in documents})

    assess_trust(documents, facts)

    ctx = CheckContext(facts=facts, documents=documents, backend=traced, policy_text=policy.text,
                       description=claim.description)
    exclusions, coverages = policy.evaluate(ctx)
    decision = policy.decide(exclusions, coverages, facts)

    result = ClaimResult(
        claim_id=claim.claim_id, policy=policy.name, description=claim.description,
        submitted_at=claim.submitted_at, processed_at=datetime.now(timezone.utc),
        documents=documents, facts=facts.as_list(), exclusions=exclusions, coverages=coverages,
        decision=decision,
    )
    if explain:
        result.decision.explanation = narrate(result, traced)
    result.calls = tracer.calls
    return result
