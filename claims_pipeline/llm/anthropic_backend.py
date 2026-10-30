"""The real model behind the four components, through the Anthropic SDK.

Every call uses structured outputs (a Pydantic model parsed by the SDK), the
per-component model and effort from Settings, and a cached system prefix
holding the policy text so the 25-claim benchmark reads the policy from
cache after the first claim.  Usage is kept on ``last_usage`` for the tracer.
"""
from __future__ import annotations

import base64
import time
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel, Field

from claims_pipeline.domain import Fact, Reading
from claims_pipeline.prompts import prompt

from .base import BackendError
from .calllog import CallLog
from .config import Settings
from .images import sniff
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

# The prompt texts live in claims_pipeline/prompts/ (see its docstring); these names stay for
# the modules and tests that quote them.
SUBMISSION_STAMP = prompt("submission_stamp").text
ROLE = prompt("role").text + "\n\n" + SUBMISSION_STAMP


# ----------------------------------------------------------------------------- shared client
class AnthropicClient:
    """Thin wrapper: one structured-output call with usage capture.  Shared by both strategies."""

    name = "anthropic"

    def __init__(self, settings: Settings | None = None, client: anthropic.Anthropic | None = None,
                 log: CallLog | None = None):
        self.settings = settings or Settings()
        self.client = client or anthropic.Anthropic()
        self.log = log if log is not None else (CallLog(self.settings.trace_dir) if self.settings.trace else None)

    def parse(self, component: str, system: str, content: list[dict[str, Any]],
              output_model: type[T]) -> tuple[T, Usage]:
        cfg = self.settings.component(component)
        kwargs: dict[str, Any] = dict(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
            output_config={"effort": cfg.effort},
        )
        if self.settings.anthropic_thinking_summary:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self.settings.fallbacks:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        logged_request = {**{k: v for k, v in kwargs.items() if k != "output_format"},
                          "output_format": {"name": output_model.__name__,
                                            "schema": output_model.model_json_schema()}}
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"response": None, "output_text": None, "reasoning": None,
                               "parsed": None, "usage": None, "error": None}
        try:
            try:
                response = self.client.beta.messages.parse(**kwargs)
            except anthropic.APIStatusError as exc:
                rec["response"] = {"status": exc.status_code, "body": getattr(exc, "body", None)}
                kind = "rate limited" if isinstance(exc, anthropic.RateLimitError) else f"API error {exc.status_code}"
                raise BackendError(f"{kind}: {exc.message}") from exc
            except anthropic.APIConnectionError as exc:
                raise BackendError(f"connection error: {exc}") from exc
            rec["response"] = response.model_dump(mode="json", exclude={"parsed_output"})
            rec["output_text"] = "".join(getattr(b, "text", "") for b in response.content if b.type == "text")
            thinking = [getattr(b, "thinking", "") for b in response.content if b.type == "thinking"]
            rec["reasoning"] = "\n\n".join(t for t in thinking if t) or None
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            usage = Usage(
                model=response.model,
                input_tokens=response.usage.input_tokens + cache_read
                + (getattr(response.usage, "cache_creation_input_tokens", 0) or 0),
                output_tokens=response.usage.output_tokens,
                cache_read_tokens=cache_read,
            )
            rec["usage"] = usage.model_dump(exclude={"call_id"})
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise BackendError(f"model refused ({getattr(details, 'category', None)}): "
                                   f"{getattr(details, 'explanation', '')}")
            if response.stop_reason == "max_tokens":
                raise BackendError("response truncated at max_tokens")
            parsed = response.parsed_output
            if parsed is None:
                raise BackendError("no structured output in response")
            rec["parsed"] = parsed.model_dump(mode="json")
        except BackendError as exc:
            rec["error"] = str(exc)
            exc.usage = Usage(**rec["usage"]) if rec["usage"] else Usage(model=cfg.model)
            exc.usage.call_id = self._log(component, exc.usage.model, logged_request, rec, t0)
            raise
        usage.call_id = self._log(component, usage.model, logged_request, rec, t0)
        return parsed, usage

    def _log(self, component: str, model: str, request: dict[str, Any], rec: dict[str, Any],
             t0: float) -> str | None:
        if self.log is None:
            return None
        return self.log.record(component=component, provider="anthropic",
                               endpoint=str(self.client.base_url).rstrip("/") + "/v1/messages", model=model,
                               request=request, latency_ms=int((time.perf_counter() - t0) * 1000), **rec)


def image_block(data: bytes, media_type: str) -> dict[str, Any]:
    """An image content block typed by its bytes, not its extension (a JPEG named .webp is a JPEG)."""
    media_type = sniff(data) or media_type
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def render_readings(documents: list[Reading]) -> str:
    """Documents as numbered lines, the form every later component reads them in.

    What the reader saw on a document (its alteration signs) and the scorer's
    probabilities for it, when one ran, are shown; the trust level and trust
    notes are not.  Those are code's conclusions from comparing facts, and a
    judge shown them answers "inconsistent" by reading them back."""
    parts = []
    for d in documents:
        head = f"### {d.filename}  [id={d.id}, type={d.doc_type}, kind={d.kind}, language={d.language}]"
        if d.issuer:
            head += f"\nissuer: {d.issuer}"
        if d.alteration_signs:
            head += "\nalteration signs noticed by the reader: " + "; ".join(d.alteration_signs)
        if d.scores:
            head += "\nindependent scorer: " + "; ".join(
                f"{'type' if q == 'doc_type' else q} {probabilities(p)}" for q, p in d.scores.items())
        body = "\n".join(f"{i}: {line}" for i, line in enumerate(d.lines, start=1)) or "(no readable content)"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def probabilities(p: dict[str, float], top: int = 3) -> str:
    """``yes 0.82, no 0.10, unknown 0.08``: the most likely answers first."""
    return ", ".join(f"{k} {v:.2f}" for k, v in sorted(p.items(), key=lambda kv: -kv[1])[:top])


def render_facts(facts: list[Fact]) -> str:
    rows = []
    for f in facts:
        if f.value is None:
            rows.append(f"- {f.name}: unknown")
        else:
            scored = f"; independent scorer: {probabilities(f.scores)}" if f.scores else ""
            rows.append(f"- {f.name}: {f.value} (confidence {f.confidence:.2f}" + (f", {f.note}" if f.note else "")
                        + scored + ")")
    return "\n".join(rows) or "(none)"


# ----------------------------------------------------------------------------- output schemas
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
