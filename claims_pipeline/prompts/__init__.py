"""The prompts: every piece of text a model is told, one file each, next to this module.

    role.md               who the model is
    submission_stamp.md   what the "Current date is" line means
    system.md             how a component's system prompt is put together
    read_document.md      the reader's task       (slot: doc_types)
    extract_facts.md      the extractor's task    (slot: facts)
    judge.md              the judge's task
    explain.md            the narrator's task
    e2e_system.md, e2e.md the single-call strategy
    reply_format.md       the schema section for local servers (slots: schema, example)
    clef.json             Clef's questions and answer options

A prompt is identified by the sha256 of its text, so a run records exactly which
version it used and an edit is a new version, never a silent change.  Slots are
filled from the policy or the claim at call time (``{doc_types}``); everything
else in a prompt is the file's text verbatim.

While a claim is processed, every prompt rendered is noted; the call log takes
the notes with each call it writes, so a logged call names the prompt versions
it was built from.  ``tests/golden/prompts.json`` pins every render.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Iterator

DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str

    @property
    def id(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def ref(self) -> dict[str, str]:
        return {"name": self.name, "id": self.id}

    def render(self, **slots: Any) -> str:
        _note(self)
        return self.text.format_map(slots) if slots else self.text


@cache
def prompt(name: str) -> Prompt:
    """``prompt("judge")`` reads ``judge.md``; ``prompt("clef")`` reads ``clef.json``."""
    path = DIR / f"{name}.md"
    if not path.exists():
        path = DIR / f"{name}.json"
    text = path.read_text(encoding="utf-8")
    return Prompt(name, text[:-1] if text.endswith("\n") else text)


def clef() -> dict[str, Any]:
    """Clef's questions and answer options, as data."""
    p = prompt("clef")
    _note(p)
    return json.loads(p.text)


def all_prompts() -> dict[str, Prompt]:
    return {p.stem: prompt(p.stem) for p in sorted(DIR.iterdir()) if p.suffix in (".md", ".json")}


# -- which prompts went into a call -------------------------------------------------------
_used: ContextVar[list[Prompt] | None] = ContextVar("prompts_used", default=None)


def _note(p: Prompt) -> None:
    used = _used.get()
    if used is not None and p not in used:
        used.append(p)


@contextmanager
def noting() -> Iterator[None]:
    """Note the prompts rendered inside this block (one claim)."""
    token = _used.set([])
    try:
        yield
    finally:
        _used.reset(token)


def take_noted() -> list[dict[str, str]]:
    """The prompts rendered since the last call was logged, and start over."""
    used = _used.get()
    if not used:
        return []
    refs = [p.ref for p in used]
    used.clear()
    return refs
