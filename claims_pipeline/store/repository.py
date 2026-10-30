"""Saving and loading the result tree.

The pipeline builds a ``ClaimResult`` in memory; this module writes it into the tables
of ``models.py`` and reads it back.  ``load_run(save_run(result)) == result`` holds for
every result (tests/test_store.py), so the database holds the whole proof chain and
nothing else is needed to show, audit or compare a run.

    connect(url)                       an engine with foreign keys on (and WAL for SQLite)
    snapshot_policy(s, policy)         the policy as declared -> policy.id
    snapshot_prompts(s)                every prompt file's current version -> {name: id}
    add_claim(s, claim, policy)        the claim and its documents, written once -> the claim row
    start_run(s, claim_row_id, ...)    a run in progress -> run.id (its model calls attach as they happen)
    save_run(s, result, run_id=...)    the finished run's result tree -> run.id
    fail_run(s, run_id, error)         a run that ended without a result
    start_batch(s, ...)                a group of runs under one config (one benchmark execution)
    set_case(s, benchmark, name, ...)  a benchmark claim and what it is expected to get
    load_run(s, run_id)                the run as a ClaimResult
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from claims_pipeline import domain, prompts
from claims_pipeline.components.sources import IMAGE_TYPES, description_lines, document_key, file_lines, metadata_lines
from claims_pipeline.policies import policy_source

from . import models as m

if TYPE_CHECKING:  # pragma: no cover
    from claims_pipeline.components import Policy
    from claims_pipeline.pipeline import ClaimInput


def now() -> datetime:
    return datetime.now(timezone.utc)


def connect(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if url != "sqlite://" and ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


# -- the policy and the prompts, as snapshots ------------------------------------------------
def snapshot_policy(s: Session, policy: "Policy") -> str:
    """Store the policy as declared, once per version; return its id."""
    from claims_pipeline.components.checks import Judged

    source = policy_source(policy.name)
    pid = hashlib.sha256(f"{source}\n{policy.text}".encode()).hexdigest()
    if s.get(m.Policy, pid) is not None:
        return pid

    def check(position: int, section: str, c, coverage: str | None = None) -> m.PolicyCheck:
        judged = isinstance(c, Judged)
        return m.PolicyCheck(position=position, section=section, coverage=coverage, check_id=c.id,
                             kind="judged" if judged else "mechanical",
                             question=c.question if judged else c.description,
                             expect=c.expect if judged else None)

    checks = [check(i, "exclusion", c) for i, c in enumerate(policy.exclusions)]
    checks += [check(i, "red_flag", c) for i, c in enumerate(policy.red_flags)]
    for cov in policy.coverages:
        checks.append(check(0, "matches", cov.matches, cov.name))
        checks.append(m.PolicyCheck(position=0, section="proofs", coverage=cov.name, check_id=f"proofs:{cov.name}",
                                    kind="proof", question=cov.proofs.describe(), expect=None))
        checks += [check(i, "checks", c, cov.name) for i, c in enumerate(cov.checks)]
    s.add(m.Policy(
        id=pid, name=policy.name, title=policy.title, currency=policy.currency, wording=policy.text, source=source,
        created_at=now(),
        facts=[m.PolicyFact(position=i, name=f.name, type=f.type, hint=f.hint) for i, f in enumerate(policy.facts)],
        doc_types=[m.PolicyDocType(position=i, name=k, description=v) for i, (k, v) in enumerate(policy.doc_types.items())],
        coverages=[m.PolicyCoverage(position=i, name=c.name, title=c.title, proofs=c.proofs.describe(),
                                    payout=c.payout.expr if c.payout else None) for i, c in enumerate(policy.coverages)],
        checks=checks,
    ))
    s.flush()
    return pid


def snapshot_prompts(s: Session) -> dict[str, str]:
    """Store every prompt file's current text, once per version; return {name: id}."""
    out = {}
    for name, p in prompts.all_prompts().items():
        if s.get(m.Prompt, p.id) is None:
            s.add(m.Prompt(id=p.id, name=name, text=p.text, created_at=now()))
        out[name] = p.id
    s.flush()
    return out


# -- the claim -----------------------------------------------------------------------------
def add_claim(s: Session, claim: "ClaimInput", policy: str, origin: str = "submitted") -> m.Claim:
    """The claim and its documents, written once.  The row id is the sha256 of the name and
    the whole input: the same claim again returns the stored row; changed content is a new claim."""
    documents = [
        m.Document(position=n, key=document_key(n, f.filename), filename=f.filename,
                   media_type=_media_type(f.filename), sha256=hashlib.sha256(f.content).hexdigest(),
                   size=len(f.content), content=f.content)
        for n, f in enumerate(claim.files, start=1)
    ]
    identity = json.dumps([claim.claim_id, policy, claim.description,
                           claim.submitted_at.isoformat() if claim.submitted_at else None, dict(claim.metadata),
                           [(d.key, d.sha256) for d in documents]], ensure_ascii=False)
    cid = hashlib.sha256(identity.encode()).hexdigest()
    stored = s.get(m.Claim, cid)
    if stored is not None:
        return stored
    row = m.Claim(id=cid, name=claim.claim_id, origin=origin, policy=policy, description=claim.description,
                  submitted_at=claim.submitted_at, meta=dict(claim.metadata), created_at=now(), documents=documents)
    s.add(row)
    s.flush()
    return row


def _media_type(filename: str) -> str | None:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return IMAGE_TYPES.get(ext) or ("text/markdown" if ext in (".md", ".markdown") else "text/plain" if ext == ".txt" else None)


def _own_lines(claim: m.Claim, kind: str, document: m.Document | None) -> list[str]:
    """A reading's lines when no model transcribed them: the document's own text."""
    if kind == "description":
        return description_lines(claim.description)
    if kind == "metadata":
        return metadata_lines(claim.submitted_at, claim.meta)
    if document is None:
        return []
    return file_lines(document.filename, document.content) or []


# -- benchmarks ----------------------------------------------------------------------------
def benchmark(s: Session, name: str, title: str | None = None) -> m.Benchmark:
    row = s.scalar(select(m.Benchmark).where(m.Benchmark.name == name))
    if row is None:
        row = m.Benchmark(name=name, title=title or name)
        s.add(row)
        s.flush()
    return row


def set_case(s: Session, bench: m.Benchmark, name: str, claim: m.Claim, expected_outcome: str | None,
             expected_payout: float | None, note: str | None) -> m.BenchmarkCase:
    """The benchmark's case ``name`` is this claim with these expectations (the latest version wins;
    runs made on an earlier version keep pointing at that claim)."""
    case = s.scalar(select(m.BenchmarkCase).where(m.BenchmarkCase.benchmark_id == bench.id,
                                                  m.BenchmarkCase.name == name))
    if case is None:
        case = m.BenchmarkCase(benchmark_id=bench.id, position=len(bench.cases), name=name, claim_id=claim.id)
        s.add(case)
    case.claim_id, case.expected_outcome, case.expected_payout, case.note = claim.id, expected_outcome, expected_payout, note
    s.flush()
    return case


def start_batch(s: Session, name: str, title: str, config_id: str, bench: m.Benchmark | None = None) -> int:
    batch = m.Batch(name=name, title=title, config_id=config_id, benchmark_id=bench.id if bench else None,
                    started_at=now())
    s.add(batch)
    s.flush()
    return batch.id


# -- one run -------------------------------------------------------------------------------
def start_run(s: Session, claim_id: str, strategy: str, *, config_id: str | None = None, policy_id: str | None = None,
              batch_id: int | None = None) -> str:
    run = m.Run(id=uuid.uuid4().hex, claim_id=claim_id, config_id=config_id, policy_id=policy_id, batch_id=batch_id,
                strategy=strategy, status="processing", started_at=now())
    s.add(run)
    s.flush()
    return run.id


def fail_run(s: Session, run_id: str, error: str) -> None:
    run = s.get(m.Run, run_id)
    run.status, run.error, run.finished_at = "failed", error, now()
    s.flush()


def save_run(s: Session, result: domain.ClaimResult, *, run_id: str | None = None, claim_id: str | None = None,
             config_id: str | None = None, policy_id: str | None = None, batch_id: int | None = None) -> str:
    """Write a finished run's result tree: into the run ``start_run`` opened (``run_id``), or into
    a new run of the stored claim ``claim_id``."""
    run = s.get(m.Run, run_id) if run_id else None
    if run is None:
        if claim_id is None or s.get(m.Claim, claim_id) is None:
            raise LookupError("save_run needs a run from start_run or a stored claim (add_claim)")
        run = m.Run(id=run_id or uuid.uuid4().hex, claim_id=claim_id, config_id=config_id, policy_id=policy_id,
                    batch_id=batch_id, strategy=result.strategy, status="processing", started_at=now())
        s.add(run)
        s.flush()
    elif run.status != "processing":
        raise ValueError(f"run {run.id} is {run.status}; a finished run is never edited")
    claim = run.claim
    if claim.name != result.claim_id:
        raise ValueError(f"the result is for claim {result.claim_id!r}, the run for {claim.name!r}")
    run.status, run.finished_at, run.processed_at = "done", now(), result.processed_at
    s.flush()

    _save_calls(s, run, result.calls)

    by_key = {d.key: d for d in claim.documents}
    by_name = {d.filename: d for d in claim.documents}
    readings: dict[str, m.Reading] = {}
    for i, r in enumerate(result.readings):
        doc = None if r.kind in ("description", "metadata") else by_key.get(r.id) or by_name.get(r.filename)
        own = _own_lines(claim, r.kind, doc)
        row = m.Reading(
            run_id=run.id, position=i, key=r.id, document_id=doc.id if doc else None, filename=r.filename,
            kind=r.kind, media_type=r.media_type, doc_type=r.doc_type, language=r.language, issuer=r.issuer,
            transcript=None if r.lines == own else list(r.lines), alteration_signs=list(r.alteration_signs),
            trust=r.trust.value, trust_reasons=list(r.trust_reasons), summary=r.summary, scores=r.scores,
            review=r.review, call_id=r.call_id, score_call_id=r.score_call_id)
        s.add(row)
        readings[r.id] = row
    s.flush()

    facts: dict[str, m.Fact] = {}
    for i, f in enumerate(result.facts):
        row = m.Fact(run_id=run.id, position=i, name=f.name, type=f.type, value=f.value, confidence=f.confidence,
                     note=f.note, scores=f.scores, score_call_id=f.score_call_id, review=f.review,
                     observations=[m.Observation(position=j, reading_id=_reading(readings, o.document_id).id,
                                                 line=o.line, quote=o.quote, value=o.value, confidence=o.confidence,
                                                 review=o.review, call_id=o.call_id)
                                   for j, o in enumerate(f.observations)])
        s.add(row)
        facts[f.name] = row
    s.flush()

    ranks = _ranks(result.decision.determining)
    for i, c in enumerate(result.exclusions):
        _save_check(s, run, "exclusion", i, c, None, facts, readings, ranks)
    for i, c in enumerate(result.red_flags):
        _save_check(s, run, "red_flag", i, c, None, facts, readings, ranks)
    for i, cov in enumerate(result.coverages):
        row = m.CoverageResult(run_id=run.id, position=i, name=cov.name, title=cov.title, status=cov.status,
                               payout=cov.payout, payout_formula=cov.payout_formula, payout_note=cov.payout_note)
        s.add(row)
        s.flush()
        _save_check(s, run, "matches", 0, cov.match, row, facts, readings, ranks)
        _save_check(s, run, "proofs", 0, cov.proofs, row, facts, readings, ranks)
        for j, c in enumerate(cov.checks):
            _save_check(s, run, "checks", j, c, row, facts, readings, ranks)
    if ranks:
        raise ValueError(f"decision names checks the run does not have: {sorted(ranks)}")

    d = result.decision
    s.add(m.Decision(run_id=run.id, outcome=d.outcome.value, confidence=d.confidence, summary=d.summary,
                     payout=d.payout, currency=d.currency, explanation=d.explanation,
                     coverages_applied=list(d.coverages_applied), unknowns=list(d.unknowns), reviews=list(d.reviews)))
    s.flush()
    return run.id


def _save_calls(s: Session, run: m.Run, calls: list[domain.ModelCall]) -> None:
    """Attach the run's calls: a call already logged in full (by its call_id) is linked to the
    run; one that was not is stored with what the result knows of it."""
    for i, c in enumerate(calls):
        row = s.scalar(select(m.ModelCall).where(m.ModelCall.call_id == c.call_id)) if c.call_id else None
        if row is None:
            row = m.ModelCall(call_id=c.call_id, component=c.component, model=c.model)
            s.add(row)
        row.run_id, row.position = run.id, i
        row.input_tokens, row.output_tokens = c.input_tokens, c.output_tokens
        row.cache_read_tokens, row.reasoning_tokens = c.cache_read_tokens, c.reasoning_tokens
        row.latency_ms, row.cost_usd, row.ok, row.error = c.latency_ms, c.cost_usd, c.ok, c.error
    s.flush()


def _ranks(determining: list[str]) -> dict[str, list[int]]:
    ranks: dict[str, list[int]] = {}
    for rank, check_id in enumerate(determining):
        ranks.setdefault(check_id, []).append(rank)
    return ranks


def _reading(readings: dict[str, m.Reading], key: str) -> m.Reading:
    try:
        return readings[key]
    except KeyError:
        raise ValueError(f"result refers to a reading it does not have: {key!r}") from None


def _save_check(s: Session, run: m.Run, section: str, position: int, c: domain.CheckResult,
                coverage: m.CoverageResult | None, facts: dict[str, m.Fact], readings: dict[str, m.Reading],
                ranks: dict[str, list[int]]) -> None:
    rank = ranks[c.id].pop(0) if ranks.get(c.id) else None
    if c.id in ranks and not ranks[c.id]:
        del ranks[c.id]
    evidence = [m.CheckEvidence(position=i, fact_id=facts[name].id) for i, name in enumerate(c.facts_used)]
    evidence += [m.CheckEvidence(position=len(evidence) + i, reading_id=_reading(readings, key).id)
                 for i, key in enumerate(c.document_ids)]
    s.add(m.CheckResult(
        run_id=run.id, coverage_result_id=coverage.id if coverage else None, position=position, section=section,
        check_id=c.id, kind=c.kind, description=c.description, verdict=c.verdict.value, rationale=c.rationale,
        quote=c.quote, quote_line=c.quote_line, confidence=c.confidence, determining_rank=rank, call_id=c.call_id,
        review=c.review,
        evidence=evidence))


def load_run(s: Session, run_id: str) -> domain.ClaimResult:
    run = s.get(m.Run, run_id)
    if run is None:
        raise LookupError(f"no run {run_id!r}")
    claim = run.claim
    reading_keys = {r.id: r.key for r in run.readings}
    fact_names = {f.id: f.name for f in run.facts}

    def check(row: m.CheckResult) -> domain.CheckResult:
        return domain.CheckResult(
            id=row.check_id, kind=row.kind, description=row.description, verdict=domain.Verdict(row.verdict),
            rationale=row.rationale, facts_used=[fact_names[e.fact_id] for e in row.evidence if e.fact_id is not None],
            document_ids=[reading_keys[e.reading_id] for e in row.evidence if e.reading_id is not None],
            quote=row.quote, quote_line=row.quote_line, confidence=row.confidence, call_id=row.call_id,
            review=row.review)

    checks = run.checks
    by_section = lambda section, cov=None: [check(c) for c in sorted(checks, key=lambda c: c.position)  # noqa: E731
                                            if c.section == section and c.coverage_result_id == cov]
    coverages = []
    for cov in run.coverages:
        (match,), (proofs,) = by_section("matches", cov.id), by_section("proofs", cov.id)
        coverages.append(domain.CoverageResult(
            name=cov.name, title=cov.title, match=match, proofs=proofs, checks=by_section("checks", cov.id),
            status=cov.status, payout=cov.payout, payout_formula=cov.payout_formula, payout_note=cov.payout_note))

    d = run.decision
    determining = [c.check_id for c in sorted((c for c in checks if c.determining_rank is not None),
                                              key=lambda c: c.determining_rank)]
    docs = {doc.id: doc for doc in claim.documents}
    return domain.ClaimResult(
        claim_id=claim.name, policy=claim.policy, strategy=run.strategy, description=claim.description,
        submitted_at=claim.submitted_at, processed_at=run.processed_at,
        readings=[domain.Reading(
            id=r.key, filename=r.filename, kind=r.kind, doc_type=r.doc_type, language=r.language, issuer=r.issuer,
            lines=r.transcript if r.transcript is not None else _own_lines(claim, r.kind, docs.get(r.document_id)),
            alteration_signs=r.alteration_signs, trust=domain.Trust(r.trust), trust_reasons=r.trust_reasons,
            media_type=r.media_type, summary=r.summary, scores=r.scores, review=r.review, call_id=r.call_id,
            score_call_id=r.score_call_id) for r in run.readings],
        facts=[domain.Fact(
            name=f.name, type=f.type, value=f.value, confidence=f.confidence, note=f.note, scores=f.scores,
            score_call_id=f.score_call_id, review=f.review,
            observations=[domain.Observation(document_id=reading_keys[o.reading_id], line=o.line, quote=o.quote,
                                             value=o.value, confidence=o.confidence, review=o.review,
                                             call_id=o.call_id)
                          for o in f.observations]) for f in run.facts],
        exclusions=by_section("exclusion"), red_flags=by_section("red_flag"), coverages=coverages,
        decision=domain.Decision(
            outcome=domain.Outcome(d.outcome), confidence=d.confidence, summary=d.summary, determining=determining,
            coverages_applied=d.coverages_applied, payout=d.payout, currency=d.currency, unknowns=d.unknowns,
            reviews=d.reviews or [], explanation=d.explanation),
        calls=[domain.ModelCall(
            component=c.component, model=c.model, call_id=c.call_id, input_tokens=c.input_tokens,
            output_tokens=c.output_tokens, cache_read_tokens=c.cache_read_tokens, reasoning_tokens=c.reasoning_tokens,
            latency_ms=c.latency_ms, cost_usd=c.cost_usd, ok=c.ok, error=c.error) for c in run.calls],
    )
