"""Typed requests and responses for every model-backed component.

Four components, four request/response pairs.  Any backend (the real model,
or a scripted oracle for tests) implements the same four calls.  The two
optional scoring components (``score_document``, ``score_facts``) return
probabilities per question.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from claims_pipeline.domain import ClaimResult, Reading, Fact


class FactRequest(BaseModel):
    name: str
    type: str
    hint: str = ""


class DocumentReadRequest(BaseModel):
    document_id: str
    filename: str
    kind: str
    media_type: str | None = None
    text: str | None = None
    image: bytes | None = None
    doc_types: dict[str, str]
    policy_name: str


class DocumentReading(BaseModel):
    doc_type: str
    language: str = "en"
    issuer: str | None = None
    lines: list[str] | None = Field(default=None, description="transcript lines; None keeps the file's own lines")
    alteration_signs: list[str] = Field(default_factory=list)
    summary: str | None = None
    review: str | None = None


class ExtractRequest(BaseModel):
    description: str
    readings: list[Reading]
    facts: list[FactRequest]
    policy_text: str


class DocumentScoreRequest(BaseModel):
    """One document as the reader got it, and what the reader concluded."""
    read: DocumentReadRequest
    reading: DocumentReading


class FactToScore(BaseModel):
    name: str
    hint: str = ""
    value: str  # as displayed, e.g. "2025-02-01 to 2025-04-10"


class FactScoreRequest(BaseModel):
    description: str
    readings: list[Reading]
    facts: list[FactToScore]
    policy_text: str


Scores = dict[str, float]  # probability per allowed answer


class ExtractedObservation(BaseModel):
    fact: str
    document_id: str
    line: int | None = None
    quote: str = ""
    value: object = None
    confidence: float = 1.0
    review: str | None = None


class JudgeRequest(BaseModel):
    check_id: str
    question: str
    expect: str
    description: str
    readings: list[Reading]
    facts: list[Fact]
    policy_text: str


class Judgment(BaseModel):
    answer: Literal["yes", "no", "unknown"]
    quote: str = ""
    document_id: str | None = None
    line: int | None = None
    rationale: str = ""
    confidence: float = 1.0
    review: str | None = None


class ExplainRequest(BaseModel):
    result: ClaimResult


class Usage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    call_id: str | None = None          # the call's line in the call log
