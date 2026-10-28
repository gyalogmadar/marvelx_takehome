"""Pick the model client for the configured backend.

``scripted`` has no client (answers come from an oracle file); ``anthropic``
talks to the Claude API through the Anthropic SDK; ``openai`` talks to any
OpenAI-compatible server (a local llama.cpp, vLLM, LM Studio, a LiteLLM
gateway).  Both real clients expose the same ``parse`` call, so the decomposed
backend and the end-to-end strategy do not care which one they got.
"""
from __future__ import annotations

from .base import BackendError
from .config import Settings

REAL_BACKENDS = ("anthropic", "openai")


def make_client(settings: Settings | None = None):
    settings = settings or Settings()
    if settings.backend == "anthropic":
        from .anthropic_backend import AnthropicClient

        return AnthropicClient(settings)
    if settings.backend == "openai":
        from .openai_backend import OpenAIClient

        return OpenAIClient(settings)
    raise BackendError(f"backend {settings.backend!r} has no model client; set CLAIMS_BACKEND to "
                       f"one of {REAL_BACKENDS}")


def make_decomposed_backend(settings: Settings | None = None):
    """The four-component backend on top of whichever client the settings name."""
    from .anthropic_backend import AnthropicBackend

    settings = settings or Settings()
    return AnthropicBackend(settings, make_client(settings))
