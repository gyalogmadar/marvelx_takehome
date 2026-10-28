"""Per-component model configuration and price table.

Every model-backed component can run on its own model and effort level.  The
defaults put everything on Claude Opus 5.5; changing one row here is the
whole cost experiment for that component.
"""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODEL = "claude-opus-5-5"

# USD per million tokens: (input, output, cache read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "scripted": (0.0, 0.0, 0.0),
}


class ComponentConfig(BaseModel):
    model: str
    effort: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAIMS_", env_file=".env", extra="ignore")

    backend: str = "scripted"              # "scripted" | "anthropic" | "openai" (any OpenAI-compatible server)
    model: str = DEFAULT_MODEL
    openai_base_url: str = ""              # e.g. http://liviuspc.tailcf3cc3.ts.net:8081/v1 (backend=openai)
    openai_api_key: str = ""               # sent as a Bearer token when set; local servers usually need none
    openai_timeout_s: float = 600.0        # a 27B model on one GPU can take minutes on a long document
    openai_disable_thinking: bool = True   # Qwen3-style chat templates: skip the <think> phase for JSON replies
    temperature: float = 0.0
    model_read_document: str | None = None
    model_extract_facts: str | None = None
    model_judge: str | None = None
    model_explain: str | None = None
    model_e2e: str | None = None
    effort_read_document: str = "medium"
    effort_extract_facts: str = "high"
    effort_judge: str = "high"
    effort_explain: str = "low"
    effort_e2e: str = "high"
    max_tokens: int = 16000
    fallbacks: bool = True                 # server-side refusal fallback (Claude API only; disable behind a gateway)
    data_dir: str = "data"
    default_policy: str = "cfsr"

    def component(self, name: str) -> ComponentConfig:
        model = getattr(self, f"model_{name}", None) or self.model
        effort = getattr(self, f"effort_{name}")
        return ComponentConfig(model=model, effort=effort)

    def components(self) -> dict[str, ComponentConfig]:
        from .base import COMPONENTS

        return {c: self.component(c) for c in COMPONENTS}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> float:
    price_in, price_out, price_cache = PRICES.get(model, (0.0, 0.0, 0.0))
    return round((input_tokens - cache_read_tokens) * price_in / 1e6 + cache_read_tokens * price_cache / 1e6
                 + output_tokens * price_out / 1e6, 6)
