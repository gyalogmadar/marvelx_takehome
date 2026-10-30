"""The Clef strategy: one request to Clef, the whole claim, a decision.

The third column of the comparison.  Clef is a decision model: it reads the
policy, the claim and the documents (images as images) and returns a
probability per answer to two questions, which outcome and which section of
the policy.  It writes no text and no numbers, so there is no reasoning to
show and no payout: an APPROVE here carries no amount.  The claim is built
exactly as for the generative end-to-end strategy, so the two single-call
strategies see the same input; images go as PNG or JPEG, as to any local
server.  Clef reads at most four images; any further
ones are named in the state but not attached, and listed as a concern.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from claims_pipeline.components import Policy
from claims_pipeline.components.sources import IMAGE_TYPES, TEXT_TYPES, description_lines, document_key, file_lines
from claims_pipeline.domain import ClaimResult, Decision, Reading, ModelCall, Outcome
from claims_pipeline.e2e import build_content
from claims_pipeline.llm.base import BackendError
from claims_pipeline.llm.calllog import claim_scope
from claims_pipeline.llm.clef import ClefClient, choice_question, read_choice
from claims_pipeline.llm.openai_client import data_url
from claims_pipeline.llm.types import Usage
from claims_pipeline.prompts import clef, prompt
from claims_pipeline.domain import ClaimInput

MAX_IMAGES = 4

OUTCOMES: dict[str, str] = json.loads(prompt("clef").text)["outcome_options"]  # prompts/clef.json


def coverage_options(policy: Policy) -> dict[str, str]:
    options = {c.name: f"{c.title}: {c.matches.description}" for c in policy.coverages}
    options["none"] = clef()["no_coverage_option"]
    return options


def state_and_images(claim: ClaimInput, policy: Policy) -> tuple[str, list[str], list[str]]:
    """The state text, the attached images as data URLs, and the images left out."""
    texts, images, left_out = [prompt("submission_stamp").render(), f"# The insurance policy\n\n{policy.text}"], [], []
    pending_name = None
    for block in build_content(claim):
        if block["type"] == "text":
            if block["text"].startswith("# Document: ") and block["text"].endswith("(image)"):
                pending_name = block["text"][len("# Document: "):-len(" (image)")]
                continue
            texts.append(block["text"])
        else:
            if len(images) < MAX_IMAGES:
                images.append(data_url(block))
                texts.append(f"# Document: {pending_name} (image {len(images)}, attached)")
            else:
                left_out.append(pending_name)
                texts.append(f"# Document: {pending_name} (image, not attached: Clef reads at most {MAX_IMAGES})")
    return "\n\n".join(texts), images, left_out


def _readings(claim: ClaimInput) -> list[Reading]:
    docs = [Reading(id="description", filename="description", kind="description", doc_type="claim_description",
                     lines=description_lines(claim.description))]
    for n, f in enumerate(claim.files, start=1):
        ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
        doc_id = document_key(n, f.filename)
        if ext in IMAGE_TYPES:
            docs.append(Reading(id=doc_id, filename=f.filename, kind="image", media_type=IMAGE_TYPES[ext]))
            continue
        lines = file_lines(f.filename, f.content)
        if lines is None:
            docs.append(Reading(id=doc_id, filename=f.filename, kind="text", doc_type="unreadable"))
            continue
        docs.append(Reading(id=doc_id, filename=f.filename, kind=TEXT_TYPES.get(ext, "text"), lines=lines))
    return docs


def process_claim_clef(claim: ClaimInput, policy: Policy, client: ClefClient) -> ClaimResult:
    state, images, left_out = state_and_images(claim, policy)
    coverages = coverage_options(policy)
    c = clef()
    questions = {
        "outcome": choice_question(c["outcome_question"], c["outcome_options"]),
        "coverage": choice_question(c["coverage_question"], coverages),
    }
    t0 = time.perf_counter()
    usage: Usage | None = None
    try:
        with claim_scope(claim.claim_id, "clef"):
            data, usage = client.ask("clef", state, questions, images)
        outcome, p_outcome = read_choice(data, "outcome", OUTCOMES)
        coverage, p_coverage = read_choice(data, "coverage", coverages)
    except BackendError as exc:
        usage = exc.usage or usage or Usage(model=client.settings.clef_model)
        decision = Decision(outcome=Outcome.UNCERTAIN, confidence=0.0, currency=policy.currency,
                            summary="Referred for review. The model call failed.",
                            unknowns=[f"1 model call(s) failed, a rerun may resolve them: clef: {exc}"])
        return _result(claim, policy, decision, usage, int((time.perf_counter() - t0) * 1000), str(exc))

    latency = int((time.perf_counter() - t0) * 1000)
    section = next((c.title for c in policy.coverages if c.name == coverage), "no covered section")
    concerns = [f"{name} was not shown to Clef (at most {MAX_IMAGES} images)" for name in left_out]
    shown = lambda probs: ", ".join(f"{k} {v:.2f}" for k, v in sorted(probs.items(), key=lambda kv: -kv[1]))
    decision = Decision(
        outcome=Outcome(outcome), confidence=p_outcome[outcome], currency=policy.currency,
        summary=f"{outcome.capitalize()} by Clef ({p_outcome[outcome]:.2f}); section: {section} ({p_coverage[coverage]:.2f}).",
        coverages_applied=[coverage] if outcome == "APPROVE" and coverage != "none" else [],
        unknowns=concerns if outcome == "UNCERTAIN" else [],
        explanation=(f"Clef answers with probabilities only; it gives no reasoning and computes no payout.\n\n"
                     f"Outcome: {shown(p_outcome)}\nSection: {shown(p_coverage)}"
                     + ("\n\nConcerns:\n" + "\n".join(f"- {c}" for c in concerns) if concerns else "")),
    )
    return _result(claim, policy, decision, usage, latency, None)


def _result(claim: ClaimInput, policy: Policy, decision: Decision, usage: Usage, latency: int,
            error: str | None) -> ClaimResult:
    return ClaimResult(
        claim_id=claim.claim_id, policy=policy.name, strategy="clef", description=claim.description,
        submitted_at=claim.submitted_at, processed_at=datetime.now(timezone.utc),
        readings=_readings(claim), facts=[], exclusions=[], coverages=[], decision=decision,
        calls=[ModelCall(component="clef", model=usage.model, call_id=usage.call_id, input_tokens=usage.input_tokens,
                         latency_ms=latency, ok=error is None, error=error)],
    )
