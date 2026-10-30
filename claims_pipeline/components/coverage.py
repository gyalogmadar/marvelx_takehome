"""Coverage: one insured section of a policy.

A coverage says what it covers (a match question), what proof it demands
(a proof rule over document types), what else must hold (checks), and what
it pays (a formula).  Evaluating it yields a CoverageResult with a status:

    eligible        matched, proofs present, every check passed, payout known
    not_applicable  the match question answered no
    ineligible      it cannot pay: matched but a proof is missing or a check failed,
                    or its required proof is missing whether it matches or not
    unresolved      something needed is unknown (including the payout inputs)

The match question gates the rest: when it is unknown, the remaining checks are
not run.  If the coverage's required proof is missing anyway, it cannot pay
whichever way the question would go, so it is ineligible rather than left open:
an unanswerable question about a section that could never pay must not hold up
a decision.  Otherwise it is unresolved, for a human.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from claims_pipeline.domain import CheckResult, CoverageResult, Reading, Trust, Verdict

from .checks import Check, CheckContext
from .quantum import Formula

ProofItem = Union[str, "ProofRule"]


@dataclass(frozen=True)
class ProofRule:
    mode: str  # "any" | "all"
    items: tuple[ProofItem, ...]

    def describe(self) -> str:
        joiner = " or " if self.mode == "any" else " and "
        parts = [f"({i.describe()})" if isinstance(i, ProofRule) else i for i in self.items]
        return joiner.join(parts)

    def doc_types(self) -> set[str]:
        out: set[str] = set()
        for i in self.items:
            out |= i.doc_types() if isinstance(i, ProofRule) else {i}
        return out

    def evaluate(self, documents: list[Reading]) -> tuple[Verdict, list[str], list[str]]:
        """Return (verdict, matched document ids, notes)."""
        verdicts: list[Verdict] = []
        matched: list[str] = []
        notes: list[str] = []
        unreadable = [d for d in documents if d.doc_type == "unreadable"]
        for item in self.items:
            if isinstance(item, ProofRule):
                v, ids, n = item.evaluate(documents)
            else:
                hits = [d for d in documents if d.doc_type == item and d.trust != Trust.UNTRUSTED]
                if hits:
                    v, ids, n = Verdict.PASS, [d.id for d in hits], [
                        f"{item}: {d.filename}" + (" (questionable)" if d.trust == Trust.QUESTIONABLE else "") for d in hits
                    ]
                elif unreadable:
                    v, ids, n = Verdict.UNKNOWN, [d.id for d in unreadable], [f"{item}: missing, but {len(unreadable)} unreadable document(s)"]
                else:
                    excluded = [d for d in documents if d.doc_type == item and d.trust == Trust.UNTRUSTED]
                    v, ids, n = Verdict.FAIL, [], [f"{item}: missing" + (" (an untrusted copy was ignored)" if excluded else "")]
            verdicts.append(v)
            matched.extend(ids)
            notes.extend(n)
        if self.mode == "any":
            verdict = Verdict.PASS if Verdict.PASS in verdicts else Verdict.UNKNOWN if Verdict.UNKNOWN in verdicts else Verdict.FAIL
        else:
            verdict = Verdict.FAIL if Verdict.FAIL in verdicts else Verdict.UNKNOWN if Verdict.UNKNOWN in verdicts else Verdict.PASS
        return verdict, sorted(set(matched)), notes


def any_of(*items: ProofItem) -> ProofRule:
    return ProofRule("any", tuple(items))


def all_of(*items: ProofItem) -> ProofRule:
    return ProofRule("all", tuple(items))


@dataclass
class Coverage:
    name: str
    title: str
    matches: Check
    proofs: ProofRule
    payout: Formula | None = None
    checks: list[Check] = field(default_factory=list)

    def evaluate(self, ctx: CheckContext) -> CoverageResult:
        match = self.matches.run(ctx)
        verdict, ids, notes = self.proofs.evaluate(ctx.readings)
        proofs = CheckResult(
            id=f"proofs:{self.name}", kind="proof", description="required proof: " + self.proofs.describe(),
            verdict=verdict, rationale="; ".join(notes) or "no documents", document_ids=ids,
            confidence=0.0 if verdict == Verdict.UNKNOWN else
            min((d.type_probability() for d in ctx.readings if d.id in ids and verdict == Verdict.PASS), default=1.0),
        )
        if match.verdict == Verdict.FAIL:
            return CoverageResult(name=self.name, title=self.title, match=match, proofs=proofs,
                                  status="not_applicable", payout_formula=self.payout.expr if self.payout else None)
        if match.verdict == Verdict.UNKNOWN:
            cannot_pay = proofs.verdict == Verdict.FAIL
            return CoverageResult(name=self.name, title=self.title, match=match, proofs=proofs,
                                  status="ineligible" if cannot_pay else "unresolved",
                                  payout_formula=self.payout.expr if self.payout else None,
                                  payout_note=None if cannot_pay else "not computed: coverage match is unresolved")

        checks = [c.run(ctx) for c in self.checks]
        results = [match, proofs, *checks]
        payout_value, payout_note = None, None
        if self.payout is not None:
            payout_value, payout_note = self.payout.evaluate(ctx.facts.numeric())

        if any(r.verdict == Verdict.FAIL for r in results):
            status = "ineligible"
        elif any(r.verdict == Verdict.UNKNOWN for r in results):
            status = "unresolved"
        elif self.payout is not None and payout_value is None:
            status = "unresolved"
        else:
            status = "eligible"
        return CoverageResult(
            name=self.name, title=self.title, match=match, proofs=proofs, checks=checks, status=status,
            payout=payout_value if status == "eligible" else None,
            payout_formula=self.payout.expr if self.payout else None, payout_note=payout_note,
        )
