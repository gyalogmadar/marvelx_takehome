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
    max_tokens: int
    thinking: bool                         # OpenAI-compatible servers: run the chat template's thinking phase


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAIMS_", env_file=".env", extra="ignore")

    backend: str = "scripted"              # "scripted" | "anthropic" | "openai" (any OpenAI-compatible server)
    model: str = DEFAULT_MODEL
    openai_base_url: str = ""              # e.g. http://liviuspc.tailcf3cc3.ts.net:8081/v1 (backend=openai)
    openai_api_key: str = ""               # sent as a Bearer token when set; local servers usually need none
    openai_timeout_s: float = 600.0        # a 27B model on one GPU can take minutes on a long document
    openai_disable_thinking: bool = True   # Qwen3-style chat templates: skip the <think> phase for every call
    # With thinking on, only these components think; reading and narrating need no deliberation.
    openai_thinking_read_document: bool = False
    openai_thinking_extract_facts: bool = True
    openai_thinking_judge: bool = True
    openai_thinking_explain: bool = False
    openai_thinking_e2e: bool = True
    # Sampling for thinking calls (Qwen's guidance: greedy decoding makes thinking loop).
    # Calls without thinking use ``temperature``.  presence_penalty curbs repetition but can
    # bend verbatim quotes, so it is off unless set.
    openai_thinking_temperature: float = 0.6
    openai_top_p: float = 0.95
    openai_top_k: int = 20
    openai_min_p: float = 0.0
    openai_presence_penalty: float = 0.0
    judge_backend: str = ""                # "" = the main backend; "clef" = Clef answers every judged check
    scorer: str = ""                       # "" = none; "clef" = Clef scores each document reading and extracted fact
    clef_base_url: str = ""                # e.g. http://liviuspc.tailcf3cc3.ts.net:8092/v1 (POST .../systemone)
    clef_model: str = "clef"
    clef_api_key: str = ""
    clef_timeout_s: float = 120.0
    clef_min_confidence: float = 0.6       # a Clef answer below this probability is UNKNOWN
    anthropic_thinking_summary: bool = True  # ask for summarized thinking so the log carries the reasoning
    temperature: float = 0.0
    trace_dir: str = "data/traces"         # the call log: calls.jsonl + blobs/ (the benchmark uses <out>/trace)
    trace: bool = True                     # log every model call in full
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
    max_tokens_read_document: int | None = None
    max_tokens_extract_facts: int | None = None
    max_tokens_judge: int | None = None
    max_tokens_explain: int | None = None
    max_tokens_e2e: int | None = None
    fallbacks: bool = True                 # server-side refusal fallback (Claude API only; disable behind a gateway)
    database_url: str = "sqlite:///data/pipeline.db"   # claims, runs and the whole proof chain (alembic upgrade head)
    default_policy: str = "cfsr"

    def component(self, name: str) -> ComponentConfig:
        model = getattr(self, f"model_{name}", None) or self.model
        effort = getattr(self, f"effort_{name}")
        max_tokens = getattr(self, f"max_tokens_{name}", None) or self.max_tokens
        thinking = not self.openai_disable_thinking and getattr(self, f"openai_thinking_{name}", True)
        return ComponentConfig(model=model, effort=effort, max_tokens=max_tokens, thinking=thinking)

    def components(self) -> dict[str, ComponentConfig]:
        from .base import COMPONENTS

        return {c: self.component(c) for c in COMPONENTS}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> float:
    price_in, price_out, price_cache = PRICES.get(model, (0.0, 0.0, 0.0))
    return round((input_tokens - cache_read_tokens) * price_in / 1e6 + cache_read_tokens * price_cache / 1e6
                 + output_tokens * price_out / 1e6, 6)
