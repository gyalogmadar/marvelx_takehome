"""The Claude API client: one structured-output call per component call, through the Anthropic SDK.

``parse(component, system, content, output_model)`` is the call every chat client offers
(see openai_client.py); the per-component model and effort come from Settings, the system
prefix holding the policy text is cached, usage is returned for the tracer, and every call
is written to the call log in full.
"""
from __future__ import annotations

import base64
import time
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from claims_pipeline.settings import Settings

from .base import BackendError
from .calllog import CallLog
from .images import sniff
from .types import Usage

T = TypeVar("T", bound=BaseModel)


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
