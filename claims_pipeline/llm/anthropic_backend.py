"""The real model behind the four components, through the Anthropic SDK.

Every call uses structured outputs (a Pydantic model parsed by the SDK), the
per-component model and effort from Settings, and a cached system prefix
holding the policy text so the 25-claim benchmark reads the policy from
cache after the first claim.  Usage is kept on ``last_usage`` for the tracer.
"""
from __future__ import annotations

import base64
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel, Field

from claims_pipeline.domain import Document, Fact

from .base import BackendError
from .config import Settings
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

T = TypeVar("T", bound=BaseModel)

ROLE = (
    "You are a meticulous insurance claims analyst. You work for the insurer but you are fair to the "
    "claimant: you read documents literally, you quote them verbatim, you never invent facts, and when "
    "something is not stated you say it is unknown rather than guessing. Documents may be in any language; "
    "answer in English but quote in the original language."
)


# ----------------------------------------------------------------------------- shared client
class AnthropicClient:
    """Thin wrapper: one structured-output call with usage capture.  Shared by both strategies."""

    name = "anthropic"

    def __init__(self, settings: Settings | None = None, client: anthropic.Anthropic | None = None):
        self.settings = settings or Settings()
        self.client = client or anthropic.Anthropic()

    def parse(self, component: str, system: str, content: list[dict[str, Any]], output_model: type[T]) -> tuple[T, Usage]:
        cfg = self.settings.component(component)
        kwargs: dict[str, Any] = dict(
            model=cfg.model,
            max_tokens=self.settings.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
            output_config={"effort": cfg.effort},
        )
        if self.settings.fallbacks:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            response = self.client.beta.messages.parse(**kwargs)
        except anthropic.RateLimitError as exc:
            raise BackendError(f"rate limited: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise BackendError(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise BackendError(f"connection error: {exc}") from exc
        usage = Usage(
            model=response.model,
            input_tokens=response.usage.input_tokens + (getattr(response.usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(response.usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise BackendError(f"model refused ({getattr(details, 'category', None)}): {getattr(details, 'explanation', '')}")
        if response.stop_reason == "max_tokens":
            raise BackendError("response truncated at max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            raise BackendError("no structured output in response")
        return parsed, usage


def image_block(data: bytes, media_type: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def render_documents(documents: list[Document]) -> str:
    """Documents as numbered lines, the form every later component reads them in."""
    parts = []
    for d in documents:
        head = f"### {d.filename}  [id={d.id}, type={d.doc_type}, kind={d.kind}, language={d.language}, trust={d.trust.value}]"
        if d.issuer:
            head += f"\nissuer: {d.issuer}"
        if d.alteration_signs:
            head += "\nalteration signs noticed by the reader: " + "; ".join(d.alteration_signs)
        if d.trust_reasons:
            head += "\ntrust notes: " + "; ".join(d.trust_reasons)
        body = "\n".join(f"{i}: {line}" for i, line in enumerate(d.lines, start=1)) or "(no readable content)"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def render_facts(facts: list[Fact]) -> str:
    rows = []
    for f in facts:
        if f.value is None:
            rows.append(f"- {f.name}: unknown")
        else:
            rows.append(f"- {f.name}: {f.value} (confidence {f.confidence:.2f}" + (f", {f.note}" if f.note else "") + ")")
    return "\n".join(rows) or "(none)"


# ----------------------------------------------------------------------------- output schemas
class ReadOut(BaseModel):
    doc_type: str = Field(description="one of the allowed document types")
    language: str = Field(description="ISO 639-1 code of the document's main language")
    issuer: str | None = Field(default=None, description="organisation that issued the document, if stated")
    lines: list[str] | None = Field(default=None, description="for images: full transcript, one visual line per entry, in the original language. For text documents: null")
    alteration_signs: list[str] = Field(default_factory=list, description="concrete visual or textual signs of alteration or fabrication; empty if none")
    summary: str = Field(description="one sentence: what this document is and what it establishes")


class ObservationOut(BaseModel):
    fact: str
    document_id: str = Field(description="the id in square brackets of the document the value was read from")
    line: int = Field(description="1-based line number within that document")
    quote: str = Field(description="the line, verbatim")
    value: str = Field(description="normalised value: dates YYYY-MM-DD, periods YYYY-MM-DD/YYYY-MM-DD, money as a plain number in EUR, integers as digits, text as written")
    confidence: float = Field(ge=0, le=1)


class ExtractOut(BaseModel):
    observations: list[ObservationOut]


class JudgmentOut(BaseModel):
    answer: Literal["yes", "no", "unknown"]
    document_filename: str | None = Field(default=None, description="filename of the document that best supports the answer, or 'description'")
    line: int | None = Field(default=None, description="1-based line number of the supporting quote")
    quote: str = Field(default="", description="verbatim supporting line, in the original language")
    rationale: str = Field(description="one or two sentences; cite documents by name")
    confidence: float = Field(ge=0, le=1)


class ExplainOut(BaseModel):
    text: str


# ----------------------------------------------------------------------------- the backend
class AnthropicBackend:
    """The four components over any client with ``parse`` (Anthropic or OpenAI-compatible)."""

    name = "anthropic"

    def __init__(self, settings: Settings | None = None, client: Any = None):
        self.settings = settings or Settings()
        self.api = client or AnthropicClient(self.settings)
        self.name = getattr(self.api, "name", "anthropic")
        self.last_usage: Usage | None = None
        self._policy_text: str = ""

    def _system(self, task: str, policy_text: str | None = None) -> str:
        policy = policy_text if policy_text is not None else self._policy_text
        return f"{ROLE}\n\n# The insurance policy\n\n{policy}\n\n# Your task in this step\n\n{task}"

    # -- read_document ------------------------------------------------------
    def read_document(self, req: DocumentReadRequest) -> DocumentReading:
        types = "\n".join(f"- {k}: {v}" for k, v in req.doc_types.items())
        task = (
            "Classify one submitted document and, if it is an image, transcribe it.\n"
            f"Allowed document types:\n{types}\n\n"
            "Rules: choose exactly one type; use 'other' when nothing fits and 'unreadable' when the content cannot be read. "
            "For an image, transcribe every visible line of text faithfully in the original language, one visual line per entry, "
            "including stamps, headers, dates and amounts; do not translate or summarise inside the transcript. "
            "For a text document, set lines to null. Report concrete alteration signs only when you actually see them "
            "(mismatched fonts, overwritten digits, misaligned text, impossible dates, inconsistent totals); otherwise an empty list."
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": f"Filename: {req.filename}\nPolicy: {req.policy_name}"}]
        if req.image is not None:
            content.append(image_block(req.image, req.media_type or "image/png"))
        else:
            content.append({"type": "text", "text": f"Document text:\n\n{req.text}"})
        out, self.last_usage = self.api.parse("read_document", self._system(task, ""), content, ReadOut)
        doc_type = out.doc_type if out.doc_type in req.doc_types else "other"
        return DocumentReading(doc_type=doc_type, language=out.language or "en", issuer=out.issuer,
                               lines=out.lines if req.image is not None else None,
                               alteration_signs=out.alteration_signs, summary=out.summary)

    # -- extract_facts ------------------------------------------------------
    def extract_facts(self, req: ExtractRequest) -> list[ExtractedObservation]:
        self._policy_text = req.policy_text
        facts = "\n".join(f"- {f.name} ({f.type}): {f.hint}" for f in req.facts)
        task = (
            "Extract the facts listed below from the claim description and the documents.\n"
            f"Facts to find:\n{facts}\n\n"
            "Rules: report one observation per place a fact appears, so a date stated in both the description and a certificate "
            "gives two observations. Quote the exact line and give its number. Normalise values as described in the schema. "
            "Never infer a value that is not written somewhere; if a fact appears nowhere, report no observation for it. "
            "Confidence reflects how unambiguous the reading is."
        )
        content = [{"type": "text", "text": f"# Claim description\n{req.description}\n\n# Documents\n{render_documents(req.documents)}"}]
        out, self.last_usage = self.api.parse("extract_facts", self._system(task), content, ExtractOut)
        known_ids = {d.id for d in req.documents}
        wanted = {f.name for f in req.facts}
        return [ExtractedObservation(fact=o.fact, document_id=o.document_id, line=o.line, quote=o.quote,
                                     value=o.value, confidence=o.confidence)
                for o in out.observations if o.fact in wanted and o.document_id in known_ids]

    # -- judge --------------------------------------------------------------
    def judge(self, req: JudgeRequest) -> Judgment:
        self._policy_text = req.policy_text
        task = (
            "Answer one yes/no question about this claim, strictly from the documents and the facts given.\n"
            "Rules: answer 'yes' or 'no' only when the documents support it; answer 'unknown' when the information is missing "
            "or genuinely ambiguous under the policy wording. Quote the single most relevant line. Your rationale must be "
            "checkable against the quote."
        )
        content = [{"type": "text", "text": (
            f"# Question\n{req.question}\n\n# Claim description\n{req.description}\n\n"
            f"# Facts already extracted\n{render_facts(req.facts)}\n\n# Documents\n{render_documents(req.documents)}")}]
        out, self.last_usage = self.api.parse("judge", self._system(task), content, JudgmentOut)
        by_name = {d.filename: d.id for d in req.documents}
        doc_id = by_name.get(out.document_filename or "", None)
        if out.document_filename == "description":
            doc_id = "description"
        return Judgment(answer=out.answer, quote=out.quote, document_id=doc_id, line=out.line,
                        rationale=out.rationale, confidence=out.confidence)

    # -- explain ------------------------------------------------------------
    def explain(self, req: ExplainRequest) -> str | None:
        r = req.result
        checks = []
        for e in r.exclusions:
            checks.append(f"- exclusion {e.id}: {e.verdict.value}. {e.rationale}")
        for c in r.coverages:
            checks.append(f"- coverage {c.title}: {c.status}" + (f", payout {c.payout}" if c.payout is not None else ""))
            for x in (c.match, c.proofs, *c.checks):
                checks.append(f"    - {x.id}: {x.verdict.value}. {x.rationale}")
        task = (
            "Write the explanation a claims handler would send with this decision. The decision has already been made by "
            "the rules below and must not be changed or second-guessed. Three to six plain sentences, addressed to the "
            "claimant, naming the documents that mattered. For a referral, say what is missing. No headings, no lists."
        )
        content = [{"type": "text", "text": (
            f"# Decision\n{r.decision.outcome.value}: {r.decision.summary}\n"
            + (f"Payout: {r.decision.payout} {r.decision.currency}\n" if r.decision.payout is not None else "")
            + (("Open points:\n" + "\n".join(f"- {u}" for u in r.decision.unknowns) + "\n") if r.decision.unknowns else "")
            + f"\n# Checks\n" + "\n".join(checks) + f"\n\n# Claim description\n{r.description}")}]
        out, self.last_usage = self.api.parse("explain", self._system(task), content, ExplainOut)
        return out.text.strip() or None
