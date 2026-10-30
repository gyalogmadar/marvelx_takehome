"""The result tree.

Everything downstream of the pipeline (API responses, the UI, the benchmark
report, the audit trail) reads this one data model.  A decision is a tree:

    ClaimResult
      decision          -> which checks determined the outcome
      exclusions[]      -> CheckResult, each naming the facts it consumed
      red_flags[]       -> CheckResult; a failing one refers the claim instead of denying it
      coverages[]       -> match, proofs, checks, payout
      facts[]           -> Fact with Observations (document + line + quote)
      readings[]        -> each document as it was read in this run: type, transcript, trust

Nothing here knows about any particular policy.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Outcome(str, Enum):
    APPROVE = "APPROVE"
    DENY = "DENY"
    UNCERTAIN = "UNCERTAIN"


class Trust(str, Enum):
    TRUSTED = "trusted"
    QUESTIONABLE = "questionable"
    UNTRUSTED = "untrusted"


DocumentKind = Literal["description", "metadata", "markdown", "text", "image"]


class Reading(BaseModel):
    """One document as it was read in one run: its type, its lines, how far it is trusted.
    The document itself (the uploaded file) never changes; two runs may read it differently.
    ``id`` names the document within the claim (``d1-booking-confirmation``); the claim
    description and the submission metadata are readings too, so facts quoting them
    carry provenance like everything else."""

    id: str
    filename: str
    kind: DocumentKind
    doc_type: str = "other"
    language: str = "en"
    issuer: str | None = None
    lines: list[str] = Field(default_factory=list)
    alteration_signs: list[str] = Field(default_factory=list)
    trust: Trust = Trust.TRUSTED
    trust_reasons: list[str] = Field(default_factory=list)
    media_type: str | None = None
    summary: str | None = None
    # The scorer's probabilities for what the reader concluded, per question: "doc_type" (one per
    # policy document type) and "altered" (yes / no / unknown).  Empty when no scorer ran.
    scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    # The reader's signal that the document holds something a claim of this kind should not:
    # always sends the claim to a person, whatever the rules decide.
    review: str | None = None
    call_id: str | None = None          # the model call that produced it: its line in the call log
    score_call_id: str | None = None    # the scorer's call, when one ran

    def type_probability(self) -> float:
        """How strongly the scorer backs the reader's document type; 1.0 when unscored."""
        return self.scores.get("doc_type", {}).get(self.doc_type, 1.0)

    def quote(self, line: int | None) -> str | None:
        if line is None or line < 1 or line > len(self.lines):
            return None
        return self.lines[line - 1]


class Observation(BaseModel):
    """A single reading of a fact from a single place in a single document."""

    document_id: str
    line: int | None = None
    quote: str = ""
    value: Any = None
    confidence: float = 1.0
    call_id: str | None = None          # the model call that produced it: its line in the call log
    review: str | None = None           # the extractor's signal: the reading is ambiguous or inferred


class Fact(BaseModel):
    """A consolidated, typed value with every observation that supports it."""

    name: str
    type: str
    value: Any = None
    confidence: float = 0.0
    observations: list[Observation] = Field(default_factory=list)
    note: str | None = None
    # The scorer's probabilities that the documents support ``value`` (yes / no / unknown);
    # when set, ``confidence`` is its "yes".  None when no scorer ran.
    scores: dict[str, float] | None = None
    score_call_id: str | None = None    # the scorer's call, when one ran
    review: str | None = None           # carried up from its observations (code, not a model)

    @property
    def known(self) -> bool:
        return self.value is not None


CheckKind = Literal["mechanical", "judged", "proof"]


class CheckResult(BaseModel):
    id: str
    kind: CheckKind
    description: str
    verdict: Verdict
    rationale: str
    facts_used: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    quote: str | None = None
    quote_line: int | None = None       # the quoted line in the first of document_ids
    confidence: float = 1.0
    review: str | None = None           # the judge's signal: the evidence is there, the wording does not settle it
    call_id: str | None = None          # the model call that produced it: its line in the call log


CoverageStatus = Literal["eligible", "not_applicable", "ineligible", "unresolved"]


class CoverageResult(BaseModel):
    name: str
    title: str
    match: CheckResult
    proofs: CheckResult
    checks: list[CheckResult] = Field(default_factory=list)
    status: CoverageStatus
    payout: float | None = None
    payout_formula: str | None = None
    payout_note: str | None = None


class Decision(BaseModel):
    outcome: Outcome
    confidence: float
    summary: str
    determining: list[str] = Field(default_factory=list, description="check ids that forced the outcome")
    coverages_applied: list[str] = Field(default_factory=list)
    payout: float | None = None
    currency: str = "EUR"
    unknowns: list[str] = Field(default_factory=list, description="what would resolve an UNCERTAIN outcome")
    reviews: list[str] = Field(default_factory=list, description="why a person must look at this claim, and who said so")
    explanation: str | None = None


class ModelCall(BaseModel):
    component: str
    model: str
    call_id: str | None = None          # the full request and response: the call log line with this id
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = True
    error: str | None = None


class ClaimResult(BaseModel):
    claim_id: str
    policy: str
    strategy: str = Field(default="decomposed", description="decomposed (components + rules), e2e (one model call) or clef (one Clef decision-model call)")
    description: str
    submitted_at: datetime | None
    processed_at: datetime
    readings: list[Reading] = Field(validation_alias=AliasChoices("readings", "documents"))
    facts: list[Fact]
    exclusions: list[CheckResult]
    red_flags: list[CheckResult] = Field(default_factory=list)
    coverages: list[CoverageResult]
    decision: Decision
    calls: list[ModelCall] = Field(default_factory=list)

    def fact(self, name: str) -> Fact | None:
        return next((f for f in self.facts if f.name == name), None)

    def reading(self, doc_id: str) -> Reading | None:
        return next((d for d in self.readings if d.id == doc_id), None)
