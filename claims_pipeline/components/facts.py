"""Fact declarations.

A policy declares the facts it needs (``Date("incident_date")``).  The
extractor builds its output schema from these declarations, so a new policy
with new facts needs no extraction code.  Each spec knows how to parse a raw
observation into a typed value and how to consolidate several observations
into one Fact, keeping every observation for the evidence chain.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Iterable

from claims_pipeline.domain import Fact, Observation


class FactSpec:
    type: ClassVar[str] = "text"

    def __init__(self, name: str, hint: str = "", required: bool = False):
        self.name = name
        self.hint = hint
        self.required = required

    # -- parsing ---------------------------------------------------------
    def parse(self, raw: Any) -> Any | None:
        """Return the typed value or None when the raw value is unusable."""
        if raw is None or raw == "":
            return None
        return str(raw).strip()

    def serialize(self, value: Any) -> Any:
        return value

    def equal(self, a: Any, b: Any) -> bool:
        return a == b

    def display(self, value: Any) -> str:
        return str(value)

    # -- consolidation ---------------------------------------------------
    def consolidate(self, observations: Iterable[Observation]) -> Fact:
        groups: dict[int, list[tuple[Any, Observation]]] = defaultdict(list)
        parsed: list[tuple[Any, Observation]] = []
        unparsable: list[Observation] = []
        for obs in observations:
            value = self.parse(obs.value)
            if value is None:
                unparsable.append(obs)
                continue
            parsed.append((value, obs))
        # group equal values
        representatives: list[Any] = []
        for value, obs in parsed:
            for idx, rep in enumerate(representatives):
                if self.equal(rep, value):
                    groups[idx].append((value, obs))
                    break
            else:
                representatives.append(value)
                groups[len(representatives) - 1].append((value, obs))

        all_obs = [o for _, o in parsed] + unparsable
        if not groups:
            note = "no usable observation" if not unparsable else "observations could not be parsed"
            return Fact(name=self.name, type=self.type, value=None, confidence=0.0,
                        observations=all_obs, note=note)

        best = max(groups, key=lambda i: sum(o.confidence for _, o in groups[i]))
        value = representatives[best]
        confidence = max(o.confidence for _, o in groups[best])
        note = None
        if len(groups) > 1:
            others = ", ".join(self.display(representatives[i]) for i in groups if i != best)
            note = f"observations disagree: {self.display(value)} vs {others}"
            confidence = round(confidence * 0.6, 3)
        return Fact(name=self.name, type=self.type, value=self.serialize(value),
                    confidence=confidence, observations=all_obs, note=note)

    def __repr__(self) -> str:  # pragma: no cover - readability only
        return f"{type(self).__name__}({self.name!r})"


class Text(FactSpec):
    type = "text"


class Int(FactSpec):
    type = "int"

    def parse(self, raw: Any) -> int | None:
        if raw is None or raw == "":
            return None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return int(raw)
        m = re.search(r"-?\d+", str(raw))
        return int(m.group()) if m else None


class Money(FactSpec):
    type = "money"

    def parse(self, raw: Any) -> float | None:
        if raw is None or raw == "":
            return None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return round(float(raw), 2)
        s = str(raw)
        s = re.sub(r"[^\d,.\-]", "", s)
        if not s:
            return None
        # "1.200,50" -> 1200.50 ; "1,200.50" -> 1200.50 ; "1200,50" -> 1200.50
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            head, _, tail = s.rpartition(",")
            s = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else s.replace(",", "")
        try:
            return round(float(s), 2)
        except ValueError:
            return None

    def equal(self, a: float, b: float) -> bool:
        return abs(a - b) < 0.005

    def display(self, value: float) -> str:
        return f"{value:,.2f}"


class Date(FactSpec):
    type = "date"

    def parse(self, raw: Any) -> date | None:
        if raw is None or raw == "":
            return None
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        s = str(raw).strip()
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None

    def serialize(self, value: date) -> str:
        return value.isoformat()

    def display(self, value: date) -> str:
        return value.isoformat()


class Period(FactSpec):
    """A start/end date pair, e.g. the policy period."""

    type = "period"

    def parse(self, raw: Any) -> tuple[date, date] | None:
        if raw is None or raw == "":
            return None
        if isinstance(raw, dict):
            start, end = raw.get("start"), raw.get("end")
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            start, end = raw
        elif isinstance(raw, str) and "/" in raw:
            start, end = raw.split("/", 1)
        else:
            return None
        d = Date("_")
        s, e = d.parse(start), d.parse(end)
        if s is None or e is None or e < s:
            return None
        return (s, e)

    def serialize(self, value: tuple[date, date]) -> dict[str, str]:
        return {"start": value[0].isoformat(), "end": value[1].isoformat()}

    def display(self, value: tuple[date, date]) -> str:
        return f"{value[0].isoformat()} to {value[1].isoformat()}"


@dataclass
class Facts:
    """The consolidated facts of one claim, with typed access for checks."""

    specs: dict[str, FactSpec]
    values: dict[str, Fact] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)  # document id -> filename, for readable rationales

    @classmethod
    def build(cls, specs: Iterable[FactSpec], observations: Iterable[tuple[str, Observation]],
              labels: dict[str, str] | None = None) -> "Facts":
        spec_map = {s.name: s for s in specs}
        grouped: dict[str, list[Observation]] = defaultdict(list)
        for name, obs in observations:
            if name in spec_map:
                grouped[name].append(obs)
        facts = cls(specs=spec_map, labels=labels or {})
        for name, spec in spec_map.items():
            facts.values[name] = spec.consolidate(grouped.get(name, []))
        return facts

    def get(self, name: str) -> Fact | None:
        return self.values.get(name)

    def typed(self, ref: str) -> Any | None:
        """Typed value for ``name`` or ``period_name.start`` / ``period_name.end``."""
        name, _, part = ref.partition(".")
        fact = self.values.get(name)
        spec = self.specs.get(name)
        if fact is None or spec is None or fact.value is None:
            return None
        value = spec.parse(fact.value)
        if part:
            if not isinstance(spec, Period) or value is None:
                return None
            return value[0] if part == "start" else value[1] if part == "end" else None
        return value

    def confidence(self, *refs: str) -> float:
        confs = [self.values[r.partition(".")[0]].confidence for r in refs if r.partition(".")[0] in self.values]
        return min(confs) if confs else 0.0

    @staticmethod
    def label(ref: str) -> str:
        """``policy_period.end`` -> ``policy period end``."""
        return ref.replace(".", " ").replace("_", " ")

    def display(self, ref: str) -> str:
        value = self.typed(ref)
        if value is None:
            return f"{self.label(ref)} (unknown)"
        name = ref.partition(".")[0]
        spec = self.specs[name]
        if isinstance(value, date):
            return value.isoformat()
        return spec.display(value)

    def source(self, ref: str) -> str:
        """Human description of where a fact came from, for rationales."""
        fact = self.values.get(ref.partition(".")[0])
        if not fact or not fact.observations:
            return ""
        obs = max(fact.observations, key=lambda o: o.confidence)
        return self.labels.get(obs.document_id, obs.document_id)

    def document_ids(self, *refs: str) -> list[str]:
        ids: list[str] = []
        for ref in refs:
            fact = self.values.get(ref.partition(".")[0])
            if fact:
                ids.extend(o.document_id for o in fact.observations)
        return sorted(set(ids))

    def numeric(self) -> dict[str, float]:
        """Known money and int facts, for payout formulas."""
        out: dict[str, float] = {}
        for name, spec in self.specs.items():
            if spec.type in ("money", "int"):
                value = self.typed(name)
                if value is not None:
                    out[name] = float(value)
        return out

    def as_list(self) -> list[Fact]:
        return list(self.values.values())
