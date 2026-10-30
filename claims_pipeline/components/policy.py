"""Policy: the composition, and the one fixed decision rule.

    any exclusion FAILS                         -> DENY
    every coverage not applicable               -> DENY   (not a covered reason)
    an eligible coverage and nothing open       -> APPROVE (sum of eligible payouts)
    nothing can pay, nothing unresolved         -> DENY   (proof or condition failed)
    otherwise                                   -> UNCERTAIN, with what would resolve it

An open exclusion only matters while something could pay: exclusions take cover
away, so when no coverage can pay the claim is denied whatever they would say.

Red flags are checks the policy wording does not make grounds for denial, such
as documents that look altered.  A red flag that FAILS refers the claim to a
human: it blocks an approval, but a denial the rules reach anyway stands.  An
open red flag blocks an approval like an open exclusion does.

Fail beats unknown beats pass, except that a coverage whose match question is
unknown stays unresolved (see coverage.py).  Code decides; the model only reads.

Review signals.  Every model component can say a person should look at something,
apart from its answer (``review`` on a reading, an observation, a judged check).
Code carries them up and attaches them to the decision; they never change it.
Shown: every reading flag, and the judge and extraction flags on what the decision
rests on.  An approval rests on every exclusion and red flag and on each applied
coverage's checks and payout facts; a denial, on its determining checks (and the
match question of a coverage that could not pay); each check, on the facts it used.
An UNCERTAIN decision shows every flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from claims_pipeline.domain import CheckResult, CoverageResult, Decision, Outcome, Reading, Verdict

from .checks import Check, CheckContext
from .coverage import Coverage
from .facts import FactSpec, Facts


@dataclass
class Policy:
    name: str
    title: str
    text: str
    facts: list[FactSpec]
    doc_types: dict[str, str]
    coverages: list[Coverage]
    exclusions: list[Check] = field(default_factory=list)
    red_flags: list[Check] = field(default_factory=list)
    currency: str = "EUR"

    def evaluate(self, ctx: CheckContext) -> tuple[list[CheckResult], list[CheckResult], list[CoverageResult]]:
        exclusions = [c.run(ctx) for c in self.exclusions]
        red_flags = [c.run(ctx) for c in self.red_flags]
        coverages = [c.evaluate(ctx) for c in self.coverages]
        return exclusions, red_flags, coverages

    def decide(self, exclusions: list[CheckResult], coverages: list[CoverageResult], facts: Facts,
               red_flags: list[CheckResult] = (), readings: list[Reading] = ()) -> Decision:
        ruled = self._rule(exclusions, coverages, facts, list(red_flags))
        return self._review(ruled, exclusions, coverages, facts, list(red_flags), list(readings))

    def _review(self, ruled: Decision, exclusions: list[CheckResult], coverages: list[CoverageResult], facts: Facts,
                red_flags: list[CheckResult], readings: list[Reading]) -> Decision:
        """Attach the flags that bear on the decision; the decision itself stands."""
        checks = exclusions + red_flags + [x for c in coverages for x in (c.match, c.proofs, *c.checks)]

        def of_check(c: CheckResult) -> list[str]:
            out = [f"judge · {c.id}: {c.review}"] if c.review else []
            for name in c.facts_used:
                fact = facts.get(name)
                if fact is not None and fact.review:
                    out.append(f"extract_facts · {name}: {fact.review}")
            return out

        always = [f"read_document · {r.filename}: {r.review}" for r in readings if r.review]
        if ruled.outcome == Outcome.APPROVE:
            applied = [c for c in coverages if c.name in ruled.coverages_applied]
            basis = exclusions + red_flags + [x for c in applied for x in (c.match, c.proofs, *c.checks)]
            payout_facts = {n for c in self.coverages if c.name in ruled.coverages_applied and c.payout
                            for n in c.payout.names()}
        elif ruled.outcome == Outcome.DENY:
            basis = [c for c in checks if c.id in ruled.determining]
            basis += [c.match for c in coverages if c.status == "ineligible" and c.match.verdict == Verdict.UNKNOWN]
            payout_facts = set()
        else:  # going to a person anyway: tell them everything that was flagged
            basis, payout_facts = checks, {f.name for f in facts.as_list() if f.review}
        flags = always + [f for c in basis for f in of_check(c)]
        flags += [f"extract_facts · {n}: {facts.get(n).review}" for n in sorted(payout_facts)
                  if facts.get(n) is not None and facts.get(n).review]
        flags = list(dict.fromkeys(flags))
        return ruled.model_copy(update={"reviews": flags}) if flags else ruled

    def _rule(self, exclusions: list[CheckResult], coverages: list[CoverageResult], facts: Facts,
              red_flags: list[CheckResult]) -> Decision:
        failed = [e for e in exclusions if e.verdict == Verdict.FAIL]
        flagged = [r for r in red_flags if r.verdict == Verdict.FAIL]
        open_exclusions = [e for e in exclusions + red_flags if e.verdict == Verdict.UNKNOWN]
        eligible = [c for c in coverages if c.status == "eligible"]
        ineligible = [c for c in coverages if c.status == "ineligible"]
        unresolved = [c for c in coverages if c.status == "unresolved"]
        not_applicable = [c for c in coverages if c.status == "not_applicable"]

        if failed:
            return Decision(
                outcome=Outcome.DENY,
                confidence=max(e.confidence for e in failed),
                summary="Denied. " + " ".join(_sentence(e.rationale) for e in failed),
                determining=[e.id for e in failed],
            )
        if coverages and len(not_applicable) == len(coverages):
            return Decision(
                outcome=Outcome.DENY,
                confidence=min(c.match.confidence for c in not_applicable),
                summary="Denied. The stated reason is not covered by any section of the policy. "
                        + " ".join(_sentence(c.match.rationale) for c in not_applicable),
                determining=[c.match.id for c in not_applicable],
            )
        if eligible and not open_exclusions and not flagged:
            involved = exclusions + [r for c in eligible for r in (c.match, c.proofs, *c.checks)]
            payout = round(sum(c.payout or 0.0 for c in eligible), 2)
            names = ", ".join(c.title for c in eligible)
            return Decision(
                outcome=Outcome.APPROVE,
                confidence=min(r.confidence for r in involved) if involved else 1.0,
                summary=f"Approved under {names}. Payout {payout:,.2f} {self.currency}.",
                determining=[r.id for c in eligible for r in (c.match, c.proofs, *c.checks)],
                coverages_applied=[c.name for c in eligible],
                payout=payout,
                currency=self.currency,
            )
        if ineligible and not unresolved and not eligible:
            failing = [r for c in ineligible for r in (c.match, c.proofs, *c.checks) if r.verdict == Verdict.FAIL]
            return Decision(
                outcome=Outcome.DENY,
                confidence=max(r.confidence for r in failing),
                summary="Denied. " + " ".join(_sentence(r.rationale) for r in failing),
                determining=[r.id for r in failing],
            )

        # UNCERTAIN: collect everything that is open, red flags first
        if flagged:
            return Decision(
                outcome=Outcome.UNCERTAIN,
                confidence=min(r.confidence for r in flagged),
                summary="Referred for review. " + " ".join(_sentence(r.rationale) for r in flagged),
                determining=[r.id for r in flagged],
                coverages_applied=[c.name for c in eligible],
                unknowns=[f"Red flag: {_open_item(r)}" for r in flagged] + [_open_item(r) for r in open_exclusions],
                currency=self.currency,
            )
        unknown_checks = open_exclusions + [
            r for c in unresolved for r in (c.match, c.proofs, *c.checks) if r.verdict == Verdict.UNKNOWN
        ]
        unknowns = [_open_item(r) for r in unknown_checks]
        unknowns += [f"payout for {c.title}: {c.payout_note}" for c in unresolved if c.payout_note and c.status == "unresolved"
                     and all(r.verdict != Verdict.UNKNOWN for r in (c.match, c.proofs, *c.checks))]
        if eligible and open_exclusions:
            unknowns.insert(0, "The claim is otherwise eligible; the open exclusion(s) decide the outcome.")
        settled = [r for r in exclusions + red_flags + [x for c in coverages for x in (c.match, c.proofs, *c.checks)]
                   if r.verdict != Verdict.UNKNOWN]
        return Decision(
            outcome=Outcome.UNCERTAIN,
            confidence=min((r.confidence for r in settled), default=0.5),
            summary="Referred for review. " + (_sentence(unknowns[0]) if unknowns else "Required information is missing."),
            determining=[r.id for r in unknown_checks],
            coverages_applied=[c.name for c in eligible],
            unknowns=unknowns,
            currency=self.currency,
        )

    def describe(self) -> dict:
        """The composition as plain data, for the policy view and the README."""
        return {
            "name": self.name,
            "title": self.title,
            "currency": self.currency,
            "facts": [{"name": f.name, "type": f.type, "hint": f.hint} for f in self.facts],
            "doc_types": self.doc_types,
            "coverages": [
                {
                    "name": c.name, "title": c.title,
                    "matches": {"id": c.matches.id, "description": c.matches.description},
                    "proofs": c.proofs.describe(),
                    "checks": [{"id": k.id, "description": k.description} for k in c.checks],
                    "payout": c.payout.expr if c.payout else None,
                }
                for c in self.coverages
            ],
            "exclusions": [{"id": e.id, "description": e.description} for e in self.exclusions],
            "red_flags": [{"id": r.id, "description": r.description} for r in self.red_flags],
        }


_ANSWER_PREFIX = re.compile(r"^answer: (yes|no|unknown)\.?\s*", re.IGNORECASE)


def _plain(text: str) -> str:
    """A check rationale without the 'answer: yes.' prefix judged checks carry."""
    return _ANSWER_PREFIX.sub("", text.strip())


def _sentence(text: str) -> str:
    text = _plain(text)
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def _open_item(r: CheckResult) -> str:
    """One line per open check: what was asked, and why it is still open."""
    why = _plain(r.rationale)
    return f"{r.description} {why}" if r.description.endswith("?") else f"{r.description}: {why}"
