"""Checks: the smallest unit of adjudication.

Every check answers pass, fail or unknown over the facts and documents of a
claim and says which facts it consumed, so the result can be traced back to
document lines.  Mechanical checks are pure Python.  ``judged`` asks the
model a yes/no question and maps the answer onto the same three verdicts.

Polarity: a check PASSES when the claim is fine.  Inside a coverage, PASS
means the condition is met; in an exclusion list, PASS means the exclusion
does not apply.  ``judged(..., expect="no")`` passes when the model answers
"no".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Protocol

from claims_pipeline.domain import CheckResult, Reading, Trust, Verdict

from .facts import Facts

if TYPE_CHECKING:  # pragma: no cover
    from claims_pipeline.llm.base import ModelBackend


@dataclass
class CheckContext:
    facts: Facts
    readings: list[Reading]
    backend: "ModelBackend"
    policy_text: str
    description: str


class Check(Protocol):
    id: str
    description: str

    def run(self, ctx: CheckContext) -> CheckResult: ...


def _unknown(check: "Check", facts: Facts, refs: list[str], kind: str = "mechanical") -> CheckResult:
    missing = [facts.label(r) for r in refs if facts.typed(r) is None]
    return CheckResult(
        id=check.id, kind=kind, description=check.description, verdict=Verdict.UNKNOWN,
        rationale="cannot evaluate, unknown: " + ", ".join(missing),
        facts_used=[r.partition(".")[0] for r in refs], document_ids=facts.document_ids(*refs),
        confidence=0.0,
    )


def _result(check: "Check", facts: Facts, refs: list[str], verdict: Verdict, rationale: str) -> CheckResult:
    return CheckResult(
        id=check.id, kind="mechanical", description=check.description, verdict=verdict,
        rationale=rationale, facts_used=[r.partition(".")[0] for r in refs],
        document_ids=facts.document_ids(*refs), confidence=facts.confidence(*refs),
    )


def _with_source(facts: Facts, ref: str) -> str:
    src = facts.source(ref)
    return f"{facts.display(ref)} ({src})" if src else facts.display(ref)


@dataclass
class WithinPeriod:
    fact: str
    period: str
    id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or f"within_period:{self.fact}"
        self.description = self.description or f"{Facts.label(self.fact)} falls inside {Facts.label(self.period)}"

    def run(self, ctx: CheckContext) -> CheckResult:
        refs = [self.fact, self.period]
        day: date | None = ctx.facts.typed(self.fact)
        period = ctx.facts.typed(self.period)
        if day is None or period is None:
            return _unknown(self, ctx.facts, refs)
        start, end = period
        inside = start <= day <= end
        rationale = (
            f"{Facts.label(self.fact)} {_with_source(ctx.facts, self.fact)} is "
            f"{'inside' if inside else 'outside'} {Facts.label(self.period)} {_with_source(ctx.facts, self.period)}"
        )
        return _result(self, ctx.facts, refs, Verdict.PASS if inside else Verdict.FAIL, rationale)


@dataclass
class Before:
    """PASS when ``first`` is on or before ``second``."""

    first: str
    second: str
    id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or f"before:{self.first}:{self.second}"
        self.description = self.description or f"{Facts.label(self.first)} is not after {Facts.label(self.second)}"

    def run(self, ctx: CheckContext) -> CheckResult:
        refs = [self.first, self.second]
        a, b = ctx.facts.typed(self.first), ctx.facts.typed(self.second)
        if a is None or b is None:
            return _unknown(self, ctx.facts, refs)
        ok = a <= b
        rationale = (
            f"{Facts.label(self.first)} {_with_source(ctx.facts, self.first)} is "
            f"{'on or before' if ok else 'after'} {Facts.label(self.second)} {_with_source(ctx.facts, self.second)}"
        )
        return _result(self, ctx.facts, refs, Verdict.PASS if ok else Verdict.FAIL, rationale)


@dataclass
class DaysBetweenAtMost:
    """PASS when ``end - start`` is at most ``days`` (negative gaps pass)."""

    start: str
    end: str
    days: int
    id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or f"days_between_at_most:{self.start}:{self.end}:{self.days}"
        self.description = self.description or f"{Facts.label(self.end)} is at most {self.days} days after {Facts.label(self.start)}"

    def run(self, ctx: CheckContext) -> CheckResult:
        refs = [self.start, self.end]
        a, b = ctx.facts.typed(self.start), ctx.facts.typed(self.end)
        if a is None or b is None:
            return _unknown(self, ctx.facts, refs)
        gap = (b - a).days
        ok = gap <= self.days
        rationale = (
            f"{Facts.label(self.end)} {_with_source(ctx.facts, self.end)} is {gap} days after "
            f"{Facts.label(self.start)} {_with_source(ctx.facts, self.start)}; limit is {self.days}"
        ) if gap >= 0 else (
            f"{Facts.label(self.end)} {_with_source(ctx.facts, self.end)} is {-gap} days before "
            f"{Facts.label(self.start)} {_with_source(ctx.facts, self.start)}; limit is {self.days} days after"
        )
        return _result(self, ctx.facts, refs, Verdict.PASS if ok else Verdict.FAIL, rationale)


@dataclass
class AgreesAcrossDocuments:
    """PASS when every observation of a fact agrees (a single source passes trivially)."""

    fact: str
    id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or f"agrees:{self.fact}"
        self.description = self.description or f"{Facts.label(self.fact)} is consistent across documents"

    def run(self, ctx: CheckContext) -> CheckResult:
        fact = ctx.facts.get(self.fact)
        if fact is None or not fact.known:
            return _unknown(self, ctx.facts, [self.fact])
        if fact.note and fact.note.startswith("observations disagree"):
            return _result(self, ctx.facts, [self.fact], Verdict.FAIL, fact.note)
        n = len(fact.observations)
        rationale = f"{Facts.label(self.fact)} = {ctx.facts.display(self.fact)} from {n} source{'s' if n != 1 else ''}, no disagreement"
        return _result(self, ctx.facts, [self.fact], Verdict.PASS, rationale)


@dataclass
class DocumentPresent:
    """PASS when a document of one of the given types is present and not untrusted."""

    types: tuple[str, ...]
    id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or "document_present:" + "|".join(self.types)
        self.description = self.description or "a " + " or ".join(self.types) + " is present"

    def run(self, ctx: CheckContext) -> CheckResult:
        hits = [d for d in ctx.readings if d.doc_type in self.types and d.trust != Trust.UNTRUSTED]
        if hits:
            return CheckResult(
                id=self.id, kind="mechanical", description=self.description, verdict=Verdict.PASS,
                rationale="present: " + ", ".join(f"{d.filename} ({d.doc_type})" for d in hits),
                document_ids=[d.id for d in hits], confidence=max(d.type_probability() for d in hits),
            )
        unreadable = [d for d in ctx.readings if d.doc_type == "unreadable"]
        if unreadable:
            return CheckResult(
                id=self.id, kind="mechanical", description=self.description, verdict=Verdict.UNKNOWN,
                rationale="not found, but unreadable documents exist: " + ", ".join(d.filename for d in unreadable),
                document_ids=[d.id for d in unreadable], confidence=0.0,
            )
        return CheckResult(
            id=self.id, kind="mechanical", description=self.description, verdict=Verdict.FAIL,
            rationale="no " + " or ".join(self.types) + " among the documents", confidence=1.0,
        )


@dataclass
class Judged:
    """A yes/no question the model answers with a quote.  PASS when answer == expect."""

    id: str
    question: str
    expect: str = "yes"
    facts_used: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.expect not in ("yes", "no"):
            raise ValueError("expect must be 'yes' or 'no'")
        self.description = self.description or self.question

    def run(self, ctx: CheckContext) -> CheckResult:
        # local imports keep components free of llm at import time
        from claims_pipeline.llm.base import BackendError, call_id_of
        from claims_pipeline.llm.types import JudgeRequest

        try:
            judgment = ctx.backend.judge(JudgeRequest(
                check_id=self.id, question=self.question, expect=self.expect,
                description=ctx.description, readings=ctx.readings,
                facts=ctx.facts.as_list(), policy_text=ctx.policy_text,
            ))
        except BackendError as exc:
            return CheckResult(
                id=self.id, kind="judged", description=self.description, verdict=Verdict.UNKNOWN,
                rationale=f"not answered, the model call failed: {exc}",
                facts_used=list(self.facts_used), confidence=0.0, call_id=call_id_of(exc),
            )
        if judgment.answer == "unknown":
            verdict = Verdict.UNKNOWN
        elif judgment.answer == self.expect:
            verdict = Verdict.PASS
        else:
            verdict = Verdict.FAIL
        doc_ids = [judgment.document_id] if judgment.document_id else []
        return CheckResult(
            id=self.id, kind="judged", description=self.description, verdict=verdict,
            rationale=f"answer: {judgment.answer}. {judgment.rationale}".strip(),
            facts_used=list(self.facts_used), document_ids=doc_ids,
            quote=judgment.quote or None, quote_line=judgment.line if doc_ids else None,
            confidence=judgment.confidence, call_id=call_id_of(ctx.backend), review=judgment.review,
        )


# -- readable constructors used in policy files --------------------------

def within_period(fact: str, period: str, **kw) -> WithinPeriod:
    return WithinPeriod(fact, period, **kw)


def before(first: str, second: str, **kw) -> Before:
    return Before(first, second, **kw)


def days_between_at_most(start: str, end: str, days: int, **kw) -> DaysBetweenAtMost:
    return DaysBetweenAtMost(start, end, days, **kw)


def agrees_across_documents(fact: str, **kw) -> AgreesAcrossDocuments:
    return AgreesAcrossDocuments(fact, **kw)


def document_present(*types: str, **kw) -> DocumentPresent:
    return DocumentPresent(tuple(types), **kw)


def judged(id: str, question: str, expect: str = "yes", facts: tuple[str, ...] = (), **kw) -> Judged:
    return Judged(id=id, question=question, expect=expect, facts_used=tuple(facts), **kw)
