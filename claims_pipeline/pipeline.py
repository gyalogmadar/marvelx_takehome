"""The pipeline: the same five steps for every policy.

    read documents -> extract facts -> assess trust -> run checks -> decide -> narrate

With a scorer routed in, each reading and the extracted facts are scored
right after they are made (components/scoring.py); the scores change
confidences, never conclusions.

Nothing in here is specific to a policy; the Policy object supplies facts,
coverages and exclusions, the backend supplies reading and judgment.

A failed model call never loses the claim.  Each component turns it into
missing information (an unreadable document, no observations, an UNKNOWN
judgment, no narrative), the rules run as usual, and the failed calls are
listed in the decision.  An approval needs all the evidence read, so one
reached while a read failed is referred instead.
"""
from __future__ import annotations

from datetime import datetime, timezone

from claims_pipeline.components import CheckContext, Facts, Policy
from claims_pipeline.components.explain import narrate
from claims_pipeline.components.scoring import score_facts
from claims_pipeline.components.sources import read_documents
from claims_pipeline.components.trust import assess_trust
from claims_pipeline.domain import ClaimInput, ClaimResult, Decision, ModelCall, Observation, Outcome
from claims_pipeline.llm.base import BackendError, ModelBackend, call_id_of
from claims_pipeline.llm.calllog import claim_scope
from claims_pipeline.llm.trace import TracedBackend, Tracer
from claims_pipeline.llm.types import ExtractRequest, FactRequest


def process_claim(claim: ClaimInput, policy: Policy, backend: ModelBackend, explain: bool = True) -> ClaimResult:
    with claim_scope(claim.claim_id, "decomposed"):
        return _process(claim, policy, backend, explain)


def _process(claim: ClaimInput, policy: Policy, backend: ModelBackend, explain: bool) -> ClaimResult:
    tracer = Tracer()
    traced = TracedBackend(backend, tracer)

    readings = read_documents(claim, policy, traced)

    try:
        extracted = traced.extract_facts(ExtractRequest(
            description=claim.description, readings=readings,
            facts=[FactRequest(name=f.name, type=f.type, hint=f.hint) for f in policy.facts],
            policy_text=policy.text,
        ))
    except BackendError:
        extracted = []  # every fact unknown; the checks that need them say so
    extract_call = call_id_of(traced)
    observations = [(o.fact, Observation(document_id=o.document_id, line=o.line, quote=o.quote,
                                         value=o.value, confidence=o.confidence, call_id=extract_call,
                                         review=o.review))
                    for o in extracted]
    # The submission date is known to the system; record it with provenance unless the extractor found one.
    if (claim.submitted_at is not None and any(f.name == "claim_submitted" for f in policy.facts)
            and not any(n == "claim_submitted" for n, _ in observations)):
        observations.append(("claim_submitted", Observation(
            document_id="metadata", line=1, quote=f"submitted_at: {claim.submitted_at.date().isoformat()}",
            value=claim.submitted_at.date().isoformat(), confidence=1.0)))
    facts = Facts.build(policy.facts, observations, labels={d.id: d.filename for d in readings})
    score_facts(traced, facts, readings, claim.description, policy.text)

    assess_trust(readings, facts)

    ctx = CheckContext(facts=facts, readings=readings, backend=traced, policy_text=policy.text,
                       description=claim.description)
    exclusions, red_flags, coverages = policy.evaluate(ctx)
    decision = account_for_failures(policy.decide(exclusions, coverages, facts, red_flags, readings), tracer.calls)

    result = ClaimResult(
        claim_id=claim.claim_id, policy=policy.name, description=claim.description,
        submitted_at=claim.submitted_at, processed_at=datetime.now(timezone.utc),
        readings=readings, facts=facts.as_list(), exclusions=exclusions, red_flags=red_flags, coverages=coverages,
        decision=decision,
    )
    if explain:
        result.decision.explanation = narrate(result, traced)
    result.calls = tracer.calls
    return result


READS = ("read_document", "extract_facts")


def account_for_failures(decision: Decision, calls: list[ModelCall]) -> Decision:
    """List failed model calls in the decision; refer an approval reached without all evidence read."""
    failed = [c for c in calls if not c.ok]
    if not failed:
        return decision
    note = f"{len(failed)} model call(s) failed, a rerun may resolve them: " + "; ".join(
        f"{c.component}: {c.error}" for c in failed)
    if decision.outcome == Outcome.APPROVE and any(c.component in READS for c in failed):
        return decision.model_copy(update=dict(
            outcome=Outcome.UNCERTAIN, payout=None,
            summary="Referred for review. The claim looks eligible, but not all evidence could be read.",
            unknowns=[note, *decision.unknowns]))
    return decision.model_copy(update=dict(unknowns=[note, *decision.unknowns]))
