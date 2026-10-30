"""Pick the model client for the configured backend.

``scripted`` has no client (answers come from an oracle file); ``anthropic``
talks to the Claude API through the Anthropic SDK; ``openai`` talks to any
OpenAI-compatible server (a local llama.cpp, vLLM, LM Studio, a LiteLLM
gateway).  Both real clients expose the same ``parse`` call, so the decomposed
backend and the end-to-end strategy do not care which one they got.

``CLAIMS_JUDGE_BACKEND=clef`` moves the judge component onto Clef, a decision
model, on top of any main backend (the scripted one included, which judges
the mock claims with Clef alone).  ``CLAIMS_SCORER=clef`` adds the two scoring
components instead: Clef puts a probability on each document reading and
each extracted fact, and the judge reads those probabilities.
"""
from __future__ import annotations

from .base import SCORERS, BackendError
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
    return with_routes(AnthropicBackend(settings, make_client(settings)), settings)


def with_routes(backend, settings: Settings):
    """The backend, with ``judge`` on the configured judge backend and the scoring components
    on the configured scorer, when either is set."""
    overrides = {}
    if settings.judge_backend:
        if settings.judge_backend != "clef":
            raise BackendError(f"unknown judge backend {settings.judge_backend!r}; supported: clef")
        from .clef_backend import ClefJudge

        overrides["judge"] = ClefJudge(settings)
    if settings.scorer:
        if settings.scorer != "clef":
            raise BackendError(f"unknown scorer {settings.scorer!r}; supported: clef")
        from .clef_backend import ClefScorer

        scorer = ClefScorer(settings)
        overrides.update(score_document=scorer, score_facts=scorer)
    return Routed(backend, **overrides) if overrides else backend


class Routed:
    """The four components, each on the backend that serves it, plus the scoring components
    when a scorer is routed in."""

    def __init__(self, base, **overrides):
        self.base, self.overrides = base, overrides
        routed = {}
        for c, b in overrides.items():  # one label per backend: "+clef score_document/score_facts"
            routed.setdefault(id(b), (b.name, []))[1].append(c)
        self.name = base.name + "".join(f"+{n} {'/'.join(cs)}" for n, cs in routed.values())
        self.last_usage = None
        for component in SCORERS:
            if component in overrides or hasattr(base, component):
                setattr(self, component, lambda req, c=component: self._run(c, req))

    def _run(self, component: str, req):
        backend = self.overrides.get(component, self.base)
        try:
            return getattr(backend, component)(req)
        finally:
            self.last_usage = backend.last_usage

    def read_document(self, req):
        return self._run("read_document", req)

    def extract_facts(self, req):
        return self._run("extract_facts", req)

    def judge(self, req):
        return self._run("judge", req)

    def explain(self, req):
        return self._run("explain", req)
