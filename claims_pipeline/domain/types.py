"""The result tree.

Everything downstream of the pipeline (API responses, the UI, the benchmark
report, the audit trail) reads this one data model.  A decision is a tree:

    ClaimResult
      decision          -> which checks determined the outcome
      exclusions[]      -> CheckResult, each naming the facts it consumed
      coverages[]       -> match, proofs, checks, payout
      facts[]           -> Fact with Observations (document + line + quote)
      documents[]       -> what was read, how it was classified, how trusted

Nothing here knows about any particular policy.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class Document(BaseModel):
    """One piece of evidence after it has been read."""

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


class Fact(BaseModel):
    """A consolidated, typed value with every observation that supports it."""

    name: str
    type: str
    value: Any = None
    confidence: float = 0.0
    observations: list[Observation] = Field(default_factory=list)
    note: str | None = None

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
    confidence: float = 1.0


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
    explanation: str | None = None


class ModelCall(BaseModel):
    component: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = True
    error: str | None = None


class ClaimResult(BaseModel):
    claim_id: str
    policy: str
    strategy: str = Field(default="decomposed", description="decomposed (components + rules) or e2e (one model call)")
    description: str
    submitted_at: datetime | None
    processed_at: datetime
    documents: list[Document]
    facts: list[Fact]
    exclusions: list[CheckResult]
    coverages: list[CoverageResult]
    decision: Decision
    calls: list[ModelCall] = Field(default_factory=list)

    def fact(self, name: str) -> Fact | None:
        return next((f for f in self.facts if f.name == name), None)

    def document(self, doc_id: str) -> Document | None:
        return next((d for d in self.documents if d.id == doc_id), None)
