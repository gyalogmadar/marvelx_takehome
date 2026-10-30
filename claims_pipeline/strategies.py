"""How a claim is processed: the three strategies, and what each one runs on.

    process = strategy(settings, "decomposed")
    result = process(claim, policy)

    decomposed  the policy as a workflow (pipeline.py): the four components on the
                configured backend (a chat model, or the scripted oracle), with the
                judge and the scorers on Clef when the settings say so
    e2e         one chat-model call with the whole claim (e2e.py)
    clef        one Clef request with the whole claim (clef_e2e.py)

This is the one place that decides what a strategy runs on; the API, the benchmark
runner and replay all ask here.  ``client`` stands in for every model client (replay
passes the recorded calls, tests pass fakes).
"""
from __future__ import annotations

from typing import Any, Callable

from claims_pipeline.components import Policy
from claims_pipeline.domain import ClaimInput, ClaimResult
from claims_pipeline.settings import Settings

STRATEGIES = ("decomposed", "e2e", "clef")

Process = Callable[[ClaimInput, Policy], ClaimResult]


def strategy(settings: Settings, name: str = "decomposed", *, oracle: dict | None = None, oracle_strict: bool = True,
             client: Any = None, explain: bool = True) -> Process:
    if name == "e2e":
        from claims_pipeline.e2e import process_claim_e2e
        from claims_pipeline.llm.clients import make_client

        api = client or make_client(settings)
        return lambda claim, policy: process_claim_e2e(claim, policy, api)
    if name == "clef":
        from claims_pipeline.clef_e2e import process_claim_clef
        from claims_pipeline.llm.clef import ClefClient

        api = client or ClefClient(settings)
        return lambda claim, policy: process_claim_clef(claim, policy, api)
    if name != "decomposed":
        raise ValueError(f"unknown strategy {name!r}; one of {STRATEGIES}")
    from claims_pipeline.llm.chat_backend import ChatBackend
    from claims_pipeline.llm.clients import make_client, with_routes
    from claims_pipeline.llm.scripted import ScriptedBackend
    from claims_pipeline.pipeline import process_claim

    if client is not None:
        base = ChatBackend(settings, client)
    elif settings.backend == "scripted":
        base = ScriptedBackend(oracle or {}, strict=oracle_strict)
    else:
        base = ChatBackend(settings, make_client(settings))
    backend = with_routes(base, settings, clef_client=client)
    return lambda claim, policy: process_claim(claim, policy, backend, explain=explain)
