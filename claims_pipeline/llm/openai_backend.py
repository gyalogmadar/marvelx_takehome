"""An OpenAI-compatible client (llama.cpp server, vLLM, LM Studio, LiteLLM, ...).

Same ``parse(component, system, content, output_model)`` contract as
``AnthropicClient``, so the four decomposed components and the end-to-end
strategy run unchanged against a local model.  Structured output is requested
through ``response_format`` with the Pydantic model's JSON schema (llama.cpp
turns it into a grammar); the reply is validated with the same model.  The
grammar only constrains the answer tokens and never reaches the prompt, so the
schema and an example of its shape are also appended to the system prompt;
without them a thinking model spends its whole budget guessing the format.
Thinking, sampling and the token budget are set per component.  Images
travel as data-URL ``image_url`` parts, converted to PNG unless they are PNG or
JPEG (llama.cpp drops other formats silently); this needs a vision-capable model and
server; a server that rejects them makes the image read fail loudly rather than
silently classify the document as unreadable.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from claims_pipeline.prompts import prompt

from .base import BackendError
from .calllog import CallLog
from .config import Settings
from .images import for_local_server
from .types import Usage

T = TypeVar("T", bound=BaseModel)

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


def to_openai_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-style content blocks (``text``, base64 ``image``) → OpenAI chat parts."""
    out: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text":
            out.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            out.append({"type": "image_url", "image_url": {"url": data_url(block)}})
        else:
            raise BackendError(f"unsupported content block {block.get('type')!r}")
    return out


def data_url(block: dict[str, Any]) -> str:
    """An image block as a data URL a local server can decode (PNG or JPEG, by content)."""
    src = block["source"]
    data, media_type = for_local_server(base64.standard_b64decode(src["data"]), src.get("media_type"))
    return f"data:{media_type};base64,{b64(data)}"


def extract_json(text: str) -> Any:
    """The JSON object in a reply: after any ``<think>`` block, tolerant of prose around it."""
    text = _THINK.sub("", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise BackendError(f"no JSON object in reply: {text[:200]!r}") from None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise BackendError(f"malformed JSON in reply: {exc}") from exc


def reply_format(output_model: type[BaseModel]) -> str:
    """The system-prompt section that shows the model the schema it must answer in."""
    schema = _untitled(output_model.model_json_schema())
    return prompt("reply_format").render(schema=json.dumps(schema, ensure_ascii=False),
                                         example=json.dumps(example_of(schema), ensure_ascii=False, indent=1))


def _untitled(node: Any) -> Any:
    """The schema without pydantic's ``title`` keys, which only cost tokens."""
    if isinstance(node, dict):
        return {k: _untitled(v) for k, v in node.items()
                if not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_untitled(v) for v in node]
    return node


def example_of(schema: dict[str, Any], node: dict[str, Any] | None = None) -> Any:
    """A placeholder instance of a JSON schema: every property, one item per array."""
    node = schema if node is None else node
    if "$ref" in node:
        return example_of(schema, schema["$defs"][node["$ref"].rsplit("/", 1)[-1]])
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"] or node["anyOf"]
        return example_of(schema, options[0])
    if "enum" in node:
        return "|".join(str(v) for v in node["enum"])
    kind = node.get("type")
    if kind == "object":
        return {name: example_of(schema, sub) for name, sub in node.get("properties", {}).items()}
    if kind == "array":
        return [example_of(schema, node.get("items", {}))]
    return {"integer": 0, "number": 0.0, "boolean": False, "null": None}.get(kind, "...")


class OpenAIClient:
    """``POST {base_url}/chat/completions`` with JSON-schema structured output."""

    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None,
                 log: CallLog | None = None):
        self.settings = settings or Settings()
        base = self.settings.openai_base_url.rstrip("/")
        if not base:
            raise BackendError("CLAIMS_OPENAI_BASE_URL is not set")
        headers = {"Content-Type": "application/json"}
        if self.settings.openai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.openai_api_key}"
        self.base_url = base
        self.http = http or httpx.Client(base_url=base, headers=headers,
                                         timeout=httpx.Timeout(self.settings.openai_timeout_s, connect=20.0))
        self.name = "openai"
        self.log = log if log is not None else (CallLog(self.settings.trace_dir) if self.settings.trace else None)

    def parse(self, component: str, system: str, content: list[dict[str, Any]],
              output_model: type[T]) -> tuple[T, Usage]:
        cfg = self.settings.component(component)
        schema = output_model.model_json_schema()
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": [{"role": "system", "content": f"{system}\n\n{reply_format(output_model)}"},
                         {"role": "user", "content": to_openai_content(content)}],
            "max_tokens": cfg.max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": output_model.__name__, "schema": schema, "strict": True}},
            "chat_template_kwargs": {"enable_thinking": cfg.thinking},
            **self._sampling(cfg.thinking),
        }
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"response": None, "output_text": None, "reasoning": None,
                               "parsed": None, "usage": None, "error": None}
        try:
            try:
                r = self.http.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                raise BackendError(f"connection error: {exc}") from exc
            try:
                data = r.json()
            except ValueError:
                data = {"status": r.status_code, "text": r.text}
            rec["response"] = data
            if r.status_code >= 400:
                raise BackendError(f"API error {r.status_code}: {r.text[:300]}")
            try:
                choice = data["choices"][0]
                message = choice["message"]
                text = message.get("content") or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise BackendError(f"unexpected reply shape: {str(data)[:200]}") from exc
            rec["output_text"] = text
            rec["reasoning"] = reasoning_of(message, text)
            u = data.get("usage") or {}
            usage = Usage(model=data.get("model") or cfg.model,
                          input_tokens=int(u.get("prompt_tokens") or 0),
                          output_tokens=int(u.get("completion_tokens") or 0),
                          cache_read_tokens=int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                          reasoning_tokens=int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0))
            rec["usage"] = usage.model_dump(exclude={"call_id"})
            if choice.get("finish_reason") == "length":
                raise BackendError("response truncated at max_tokens")
            try:
                parsed = output_model.model_validate(extract_json(text))
            except ValidationError as exc:
                raise BackendError(f"reply does not match {output_model.__name__}: {exc.errors()[:3]}") from exc
            rec["parsed"] = parsed.model_dump(mode="json")
        except BackendError as exc:
            rec["error"] = str(exc)
            exc.usage = Usage(**rec["usage"]) if rec["usage"] else Usage(model=cfg.model)
            exc.usage.call_id = self._log(component, exc.usage.model, body, rec, t0)
            raise
        usage.call_id = self._log(component, usage.model, body, rec, t0)
        return parsed, usage

    def _sampling(self, thinking: bool) -> dict[str, Any]:
        s = self.settings
        if not thinking:
            return {"temperature": s.temperature}
        return {"temperature": s.openai_thinking_temperature, "top_p": s.openai_top_p, "top_k": s.openai_top_k,
                "min_p": s.openai_min_p, "presence_penalty": s.openai_presence_penalty}

    def _log(self, component: str, model: str, body: dict[str, Any], rec: dict[str, Any], t0: float) -> str | None:
        if self.log is None:
            return None
        return self.log.record(component=component, provider="openai",
                               endpoint=f"{self.base_url}/chat/completions", model=model, request=body,
                               latency_ms=int((time.perf_counter() - t0) * 1000), **rec)


def reasoning_of(message: dict[str, Any], text: str) -> str | None:
    """The reasoning a reply carried: llama.cpp / vLLM ``reasoning_content`` (or ``reasoning``),
    else an inline <think> block in the content."""
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    m = re.search(r"<think>(.*?)</think>", text or "", re.S)
    return m.group(1).strip() if m else None


def b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")
