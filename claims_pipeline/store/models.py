"""The tables.  Read them against ``policies/cfsr.py`` and ``domain/types.py``: every
name is one you have already met there.

    the policy as declared     policy, policy_fact, policy_doc_type, policy_coverage, policy_check
    the prompts                prompt
    the input (written once)   claim, document
    how a run was made         run_config, batch, benchmark, benchmark_case
    one run (never edited)     run, reading, fact, observation, coverage_result, check_result,
                               check_evidence, decision, model_call

A ``document`` is the uploaded file.  A ``reading`` is a document as one run read it
(its type, its transcript, how far it is trusted); the claim description and the
submission metadata are readings too, with no document behind them.  A reading's
lines are not stored when they are the document's own: ``transcript`` holds them
only when a model transcribed them (an image).

Lists that something joins on or a person traces through are rows (observations,
check evidence); lists that are only displayed are JSON (alteration signs, unknowns).
Every list keeps its order in a ``position`` column.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import Boolean, Float, ForeignKey, LargeBinary, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# Python None is SQL NULL, not the JSON text 'null', so "IS NULL" means what it says.
JSON = SA_JSON(none_as_null=True)


class Timestamp(TypeDecorator):
    """A datetime stored as ISO 8601 text, offset included, so it loads back exactly as saved."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.isoformat() if value is not None else None

    def process_result_value(self, value, dialect):
        return datetime.fromisoformat(value) if value is not None else None


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, Any: JSON}


# -- the policy as declared in policies/<name>.py ------------------------------------------
class Policy(Base):
    """One version of a policy: the sha256 of its policy file and its wording."""
    __tablename__ = "policy"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    wording: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)          # the policy file, as it was
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    facts: Mapped[list[PolicyFact]] = relationship(order_by="PolicyFact.position")
    doc_types: Mapped[list[PolicyDocType]] = relationship(order_by="PolicyDocType.position")
    coverages: Mapped[list[PolicyCoverage]] = relationship(order_by="PolicyCoverage.position")
    checks: Mapped[list[PolicyCheck]] = relationship(order_by="PolicyCheck.position")


class PolicyFact(Base):
    """``Date("incident_date", "the date the covered event happened ...")``"""
    __tablename__ = "policy_fact"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policy.id"))
    position: Mapped[int]
    name: Mapped[str]
    type: Mapped[str]
    hint: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("policy_id", "name"),)


class PolicyDocType(Base):
    """One entry of ``doc_types={...}``."""
    __tablename__ = "policy_doc_type"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policy.id"))
    position: Mapped[int]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("policy_id", "name"),)


class PolicyCoverage(Base):
    """``Coverage(name, title, matches, proofs, payout, checks)``: proofs and payout as written."""
    __tablename__ = "policy_coverage"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policy.id"))
    position: Mapped[int]
    name: Mapped[str]
    title: Mapped[str]
    proofs: Mapped[str] = mapped_column(Text)          # "(medical_report or ...) and booking_confirmation"
    payout: Mapped[str | None] = mapped_column(Text)   # "0.5 * booking_value / passengers * affected"
    __table_args__ = (UniqueConstraint("policy_id", "name"),)


class PolicyCheck(Base):
    """One check as declared.  ``section``: exclusion | red_flag (policy level) or matches |
    proofs | checks (inside ``coverage``).  ``question`` is the judged question or the
    mechanical check's description; ``expect`` the answer that passes a judged check."""
    __tablename__ = "policy_check"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policy.id"))
    position: Mapped[int]
    section: Mapped[str]
    coverage: Mapped[str | None]
    check_id: Mapped[str]
    kind: Mapped[str]                                   # judged | mechanical | proof
    question: Mapped[str] = mapped_column(Text)
    expect: Mapped[str | None]
    __table_args__ = (UniqueConstraint("policy_id", "section", "coverage", "check_id"),)


# -- prompts -------------------------------------------------------------------------------
class Prompt(Base):
    """One version of a file in claims_pipeline/prompts/: its sha256 and its text."""
    __tablename__ = "prompt"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp)


# -- the input: written once, never edited ---------------------------------------------------
class Claim(Base):
    """A claim as submitted.  ``name`` is how people and results refer to it (the API's claim id,
    a benchmark folder); ``id`` is the sha256 of the name and the whole input, so a claim whose
    content changes (a regenerated mock) is a new claim and the old runs keep the old one."""
    __tablename__ = "claim"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(index=True)
    origin: Mapped[str]                                 # submitted (through the API) | benchmark
    policy: Mapped[str]                                 # the policy's name; the version is the run's
    description: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(Timestamp)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    documents: Mapped[list[Document]] = relationship(order_by="Document.position")
    runs: Mapped[list[Run]] = relationship(back_populates="claim", order_by="Run.started_at")


class Document(Base):
    """An uploaded file.  ``key`` is how a run refers to it (``d1-booking-confirmation``)."""
    __tablename__ = "document"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim.id"))
    position: Mapped[int]
    key: Mapped[str]
    filename: Mapped[str]
    media_type: Mapped[str | None]
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]
    content: Mapped[bytes] = mapped_column(LargeBinary)
    __table_args__ = (UniqueConstraint("claim_id", "key"),)


# -- how runs were made ----------------------------------------------------------------------
class RunConfig(Base):
    """Everything a run was made with, hashed: code, policy, prompts, strategy, models, settings."""
    __tablename__ = "run_config"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp)


class Benchmark(Base):
    """A set of claims with expected outcomes (``mockclaims``, the take-home claims)."""
    __tablename__ = "benchmark"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    cases: Mapped[list[BenchmarkCase]] = relationship(order_by="BenchmarkCase.position")


class BenchmarkCase(Base):
    __tablename__ = "benchmark_case"
    id: Mapped[int] = mapped_column(primary_key=True)
    benchmark_id: Mapped[int] = mapped_column(ForeignKey("benchmark.id"))
    position: Mapped[int]
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim.id"))
    name: Mapped[str]                                   # the claim folder, e.g. 08-altered-booking-image
    expected_outcome: Mapped[str | None]
    expected_payout: Mapped[float | None]
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("benchmark_id", "name"),)


class Batch(Base):
    """One execution of a benchmark (or any group of runs) under one config."""
    __tablename__ = "batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(index=True)      # the output folder (results/<name>); reruns share it
    title: Mapped[str]
    benchmark_id: Mapped[int | None] = mapped_column(ForeignKey("benchmark.id"))
    config_id: Mapped[str] = mapped_column(ForeignKey("run_config.id"))
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    runs: Mapped[list[Run]] = relationship(back_populates="batch")


# -- one run: append-only --------------------------------------------------------------------
class Run(Base):
    __tablename__ = "run"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim.id"), index=True)
    config_id: Mapped[str | None] = mapped_column(ForeignKey("run_config.id"))
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policy.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batch.id"), index=True)
    strategy: Mapped[str]
    status: Mapped[str]                                 # processing | done | failed
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)
    processed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    error: Mapped[str | None] = mapped_column(Text)
    claim: Mapped[Claim] = relationship(back_populates="runs")
    batch: Mapped[Batch | None] = relationship(back_populates="runs")
    readings: Mapped[list[Reading]] = relationship(order_by="Reading.position")
    facts: Mapped[list[Fact]] = relationship(order_by="Fact.position")
    coverages: Mapped[list[CoverageResult]] = relationship(order_by="CoverageResult.position")
    checks: Mapped[list[CheckResult]] = relationship(order_by="CheckResult.position")
    decision: Mapped[Decision | None] = relationship()
    calls: Mapped[list[ModelCall]] = relationship(order_by="ModelCall.position")


class ModelCall(Base):
    """One model call, complete.  ``call_id`` is the id every result row refers to."""
    __tablename__ = "model_call"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str | None] = mapped_column(String, unique=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id"), index=True)
    position: Mapped[int | None]
    component: Mapped[str]
    provider: Mapped[str | None]
    endpoint: Mapped[str | None]
    model: Mapped[str]
    prompts: Mapped[list[Any] | None] = mapped_column(JSON)    # [{name, id}] -> prompt.id
    request: Mapped[Any | None] = mapped_column(JSON)
    response: Mapped[Any | None] = mapped_column(JSON)
    output_text: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[Any | None] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    reasoning_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Reading(Base):
    """A document as this run read it; ``key`` is the id the result tree uses."""
    __tablename__ = "reading"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    position: Mapped[int]
    key: Mapped[str]                                    # d1-booking-confirmation | description | metadata
    document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id"))
    filename: Mapped[str]
    kind: Mapped[str]
    media_type: Mapped[str | None]
    doc_type: Mapped[str]
    language: Mapped[str]
    issuer: Mapped[str | None]
    transcript: Mapped[list[Any] | None] = mapped_column(JSON)  # lines, only when a model transcribed them
    alteration_signs: Mapped[list[Any]] = mapped_column(JSON)
    trust: Mapped[str]
    trust_reasons: Mapped[list[Any]] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON)
    review: Mapped[str | None] = mapped_column(Text)    # the reader: something unexpected; always sends to a person
    call_id: Mapped[str | None] = mapped_column(ForeignKey("model_call.call_id"))
    score_call_id: Mapped[str | None] = mapped_column(ForeignKey("model_call.call_id"))
    document: Mapped[Document | None] = relationship()
    __table_args__ = (UniqueConstraint("run_id", "key"),)


class Fact(Base):
    __tablename__ = "fact"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    position: Mapped[int]
    name: Mapped[str]                                   # policy_fact.name
    type: Mapped[str]
    value: Mapped[Any | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    score_call_id: Mapped[str | None] = mapped_column(ForeignKey("model_call.call_id"))
    review: Mapped[str | None] = mapped_column(Text)    # carried up from its observations
    observations: Mapped[list[Observation]] = relationship(order_by="Observation.position")
    __table_args__ = (UniqueConstraint("run_id", "name"),)


class Observation(Base):
    """One place a fact was read: a reading, a line, the quote, the raw value."""
    __tablename__ = "observation"
    id: Mapped[int] = mapped_column(primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("fact.id"), index=True)
    position: Mapped[int]
    reading_id: Mapped[int] = mapped_column(ForeignKey("reading.id"))
    line: Mapped[int | None]
    quote: Mapped[str] = mapped_column(Text)
    value: Mapped[Any | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    review: Mapped[str | None] = mapped_column(Text)    # the extractor: ambiguous or inferred
    call_id: Mapped[str | None] = mapped_column(ForeignKey("model_call.call_id"))
    reading: Mapped[Reading] = relationship()


class CoverageResult(Base):
    __tablename__ = "coverage_result"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    position: Mapped[int]
    name: Mapped[str]                                   # policy_coverage.name
    title: Mapped[str]
    status: Mapped[str]                                 # eligible | not_applicable | ineligible | unresolved
    payout: Mapped[float | None]
    payout_formula: Mapped[str | None] = mapped_column(Text)
    payout_note: Mapped[str | None] = mapped_column(Text)


class CheckResult(Base):
    """One check's verdict in this run.  ``section`` and ``check_id`` are the policy's words;
    with the run's policy they name the ``policy_check`` row that was asked."""
    __tablename__ = "check_result"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    coverage_result_id: Mapped[int | None] = mapped_column(ForeignKey("coverage_result.id"))
    position: Mapped[int]                               # order within its section (and coverage)
    section: Mapped[str]                                # exclusion | red_flag | matches | proofs | checks
    check_id: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str]                                   # mechanical | judged | proof
    description: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str]                                # pass | fail | unknown
    rationale: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    quote_line: Mapped[int | None]
    confidence: Mapped[float] = mapped_column(Float)
    determining_rank: Mapped[int | None]                # its place in decision.determining, if there
    review: Mapped[str | None] = mapped_column(Text)    # the judge: the wording does not settle it
    call_id: Mapped[str | None] = mapped_column(ForeignKey("model_call.call_id"))
    evidence: Mapped[list[CheckEvidence]] = relationship(order_by="CheckEvidence.position")


class CheckEvidence(Base):
    """What a check used: a fact (``facts_used``) or a reading (``document_ids``)."""
    __tablename__ = "check_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    check_result_id: Mapped[int] = mapped_column(ForeignKey("check_result.id"), index=True)
    position: Mapped[int]
    fact_id: Mapped[int | None] = mapped_column(ForeignKey("fact.id"))
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("reading.id"))
    fact: Mapped[Fact | None] = relationship()
    reading: Mapped[Reading | None] = relationship()


class Decision(Base):
    __tablename__ = "decision"
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), primary_key=True)
    outcome: Mapped[str]
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    payout: Mapped[float | None]
    currency: Mapped[str]
    explanation: Mapped[str | None] = mapped_column(Text)
    coverages_applied: Mapped[list[Any]] = mapped_column(JSON)
    unknowns: Mapped[list[Any]] = mapped_column(JSON)
    reviews: Mapped[list[Any] | None] = mapped_column(JSON)  # why a person must look, and who said so
