"""An OpenAI-compatible client (llama.cpp server, vLLM, LM Studio, LiteLLM, ...).

Same ``parse(component, system, content, output_model)`` contract as
``AnthropicClient``, so the four decomposed components and the end-to-end
strategy run unchanged against a local model.  Structured output is requested
through ``response_format`` with the Pydantic model's JSON schema (llama.cpp
turns it into a grammar); the reply is validated with the same model.  Images
travel as data-URL ``image_url`` parts, which needs a vision-capable model and
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

from .base import BackendError
from .config import Settings
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
            src = block["source"]
            out.append({"type": "image_url",
                        "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
        else:
            raise BackendError(f"unsupported content block {block.get('type')!r}")
    return out


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


class OpenAIClient:
    """``POST {base_url}/chat/completions`` with JSON-schema structured output."""

    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None):
        self.settings = settings or Settings()
        base = self.settings.openai_base_url.rstrip("/")
        if not base:
            raise BackendError("CLAIMS_OPENAI_BASE_URL is not set")
        headers = {"Content-Type": "application/json"}
        if self.settings.openai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.openai_api_key}"
        self.http = http or httpx.Client(base_url=base, headers=headers,
                                         timeout=httpx.Timeout(self.settings.openai_timeout_s, connect=20.0))
        self.name = "openai"

    def parse(self, component: str, system: str, content: list[dict[str, Any]],
              output_model: type[T]) -> tuple[T, Usage]:
        cfg = self.settings.component(component)
        schema = output_model.model_json_schema()
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": to_openai_content(content)}],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": output_model.__name__, "schema": schema, "strict": True}},
        }
        if self.settings.openai_disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        t0 = time.perf_counter()
        try:
            r = self.http.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise BackendError(f"connection error: {exc}") from exc
        if r.status_code >= 400:
            raise BackendError(f"API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"unexpected reply shape: {str(data)[:200]}") from exc
        if choice.get("finish_reason") == "length":
            raise BackendError("response truncated at max_tokens")
        try:
            parsed = output_model.model_validate(extract_json(text))
        except ValidationError as exc:
            raise BackendError(f"reply does not match {output_model.__name__}: {exc.errors()[:3]}") from exc
        u = data.get("usage") or {}
        usage = Usage(model=data.get("model") or cfg.model,
                      input_tokens=int(u.get("prompt_tokens") or 0),
                      output_tokens=int(u.get("completion_tokens") or 0),
                      cache_read_tokens=int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0))
        usage_latency = int((time.perf_counter() - t0) * 1000)
        self.last_latency_ms = usage_latency
        return parsed, usage


def b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")
