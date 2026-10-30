"""The four model-backed components on a chat model: read_document, extract_facts, judge, explain.

Each component builds its system prompt from the prompt files (role, submission stamp,
the policy wording, its task) and its input from the claim (rendered by render.py),
asks the chat client for a structured answer in the schema below, and maps the answer
back into the result tree's types.  The client is Claude (anthropic_client.py) or any
OpenAI-compatible server (openai_client.py); this module does not care which.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from claims_pipeline.prompts import prompt
from claims_pipeline.settings import Settings

from .anthropic_client import AnthropicClient, image_block
from .render import render_facts, render_readings
from .types import (
    DocumentReading,
    DocumentReadRequest,
    ExplainRequest,
    ExtractedObservation,
    ExtractRequest,
    JudgeRequest,
    Judgment,
    Usage,
)


# -- what each component's model call must return ------------------------------------------
class ReadOut(BaseModel):
    doc_type: str = Field(description="one of the allowed document types")
    language: str = Field(description="ISO 639-1 code of the document's main language")
    issuer: str | None = Field(default=None, description="organisation that issued the document, if stated")
    lines: list[str] | None = Field(default=None, description="for images: full transcript, one visual line per entry, in the original language. For text documents: null")
    alteration_signs: list[str] = Field(default_factory=list, description="concrete visual or textual signs of alteration or fabrication; empty if none")
    summary: str = Field(description="one sentence: what this document is and what it establishes")
    review: str | None = Field(default=None, description="only when the document contains something unexpected for "
                               "a claim document of its kind: say what, in one sentence; otherwise null")


class ObservationOut(BaseModel):
    fact: str
    document_id: str = Field(description="the id in square brackets of the document the value was read from")
    line: int = Field(description="1-based line number within that document")
    quote: str = Field(description="the line, verbatim")
    value: str = Field(description="normalised value: dates YYYY-MM-DD, periods YYYY-MM-DD/YYYY-MM-DD, money as a plain number in EUR, integers as digits, text as written")
    confidence: float = Field(ge=0, le=1)
    review: str | None = Field(default=None, description="only when the documents support more than one value or the "
                               "value had to be inferred: say why, in one sentence; otherwise null")


class ExtractOut(BaseModel):
    observations: list[ObservationOut]


class JudgmentOut(BaseModel):
    answer: Literal["yes", "no", "unknown"]
    document_filename: str | None = Field(default=None, description="filename of the document that best supports the answer, or 'description'")
    line: int | None = Field(default=None, description="1-based line number of the supporting quote")
    quote: str = Field(default="", description="verbatim supporting line, in the original language")
    rationale: str = Field(description="one or two sentences; cite documents by name")
    confidence: float = Field(ge=0, le=1)
    review: str | None = Field(default=None, description="only when the evidence is present but the policy wording "
                               "does not settle the question: say what a person must decide; otherwise null")


class ExplainOut(BaseModel):
    text: str


class ChatBackend:
    """The four components over any chat client with ``parse`` (Claude, or an OpenAI-compatible server)."""

    name = "chat"

    def __init__(self, settings: Settings | None = None, client: Any = None):
        self.settings = settings or Settings()
        self.api = client or AnthropicClient(self.settings)
        self.name = getattr(self.api, "name", "chat")
        self.last_usage: Usage | None = None
        self._policy_text: str = ""

    def _system(self, task: str, policy_text: str | None = None) -> str:
        policy = policy_text if policy_text is not None else self._policy_text
        return prompt("system").render(role=prompt("role").render(), submission_stamp=prompt("submission_stamp").render(),
                                       policy=policy, task=task)

    # -- read_document ------------------------------------------------------
    def read_document(self, req: DocumentReadRequest) -> DocumentReading:
        types = "\n".join(f"- {k}: {v}" for k, v in req.doc_types.items())
        task = prompt("read_document").render(doc_types=types)
        content: list[dict[str, Any]] = [{"type": "text", "text": f"Filename: {req.filename}\nPolicy: {req.policy_name}"}]
        if req.image is not None:
            content.append(image_block(req.image, req.media_type or "image/png"))
        else:
            content.append({"type": "text", "text": f"Document text:\n\n{req.text}"})
        out, self.last_usage = self.api.parse("read_document", self._system(task, ""), content, ReadOut)
        doc_type = out.doc_type if out.doc_type in req.doc_types else "other"
        return DocumentReading(doc_type=doc_type, language=out.language or "en", issuer=out.issuer,
                               lines=out.lines if req.image is not None else None,
                               alteration_signs=out.alteration_signs, summary=out.summary, review=out.review or None)

    # -- extract_facts ------------------------------------------------------
    def extract_facts(self, req: ExtractRequest) -> list[ExtractedObservation]:
        self._policy_text = req.policy_text
        facts = "\n".join(f"- {f.name} ({f.type}): {f.hint}" for f in req.facts)
        task = prompt("extract_facts").render(facts=facts)
        content = [{"type": "text", "text": f"# Claim description\n{req.description}\n\n# Documents\n{render_readings(req.readings)}"}]
        out, self.last_usage = self.api.parse("extract_facts", self._system(task), content, ExtractOut)
        known_ids = {d.id for d in req.readings}
        wanted = {f.name for f in req.facts}
        return [ExtractedObservation(fact=o.fact, document_id=o.document_id, line=o.line, quote=o.quote,
                                     value=o.value, confidence=o.confidence, review=o.review or None)
                for o in out.observations if o.fact in wanted and o.document_id in known_ids]

    # -- judge --------------------------------------------------------------
    def judge(self, req: JudgeRequest) -> Judgment:
        self._policy_text = req.policy_text
        task = prompt("judge").render()
        content = [{"type": "text", "text": (
            f"# Question\n{req.question}\n\n# Claim description\n{req.description}\n\n"
            f"# Facts already extracted\n{render_facts(req.facts)}\n\n# Documents\n{render_readings(req.readings)}")}]
        out, self.last_usage = self.api.parse("judge", self._system(task), content, JudgmentOut)
        by_name = {d.filename: d.id for d in req.readings}
        doc_id = by_name.get(out.document_filename or "", None)
        if out.document_filename == "description":
            doc_id = "description"
        return Judgment(answer=out.answer, quote=out.quote, document_id=doc_id, line=out.line,
                        rationale=out.rationale, confidence=out.confidence, review=out.review or None)

    # -- explain ------------------------------------------------------------
    def explain(self, req: ExplainRequest) -> str | None:
        r = req.result
        checks = []
        for e in r.exclusions:
            checks.append(f"- exclusion {e.id}: {e.verdict.value}. {e.rationale}")
        for f in r.red_flags:
            checks.append(f"- red flag {f.id}: {f.verdict.value}. {f.rationale}")
        for c in r.coverages:
            checks.append(f"- coverage {c.title}: {c.status}" + (f", payout {c.payout}" if c.payout is not None else ""))
            for x in (c.match, c.proofs, *c.checks):
                checks.append(f"    - {x.id}: {x.verdict.value}. {x.rationale}")
        task = prompt("explain").render()
        content = [{"type": "text", "text": (
            f"# Decision\n{r.decision.outcome.value}: {r.decision.summary}\n"
            + (f"Payout: {r.decision.payout} {r.decision.currency}\n" if r.decision.payout is not None else "")
            + (("Open points:\n" + "\n".join(f"- {u}" for u in r.decision.unknowns) + "\n") if r.decision.unknowns else "")
            + f"\n# Checks\n" + "\n".join(checks) + f"\n\n# Claim description\n{r.description}")}]
        out, self.last_usage = self.api.parse("explain", self._system(task), content, ExplainOut)
        return out.text.strip() or None
