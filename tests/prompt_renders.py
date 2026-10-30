"""Every prompt the system sends, rendered for fixed claims, so a change to any prompt shows
up as a diff.  ``tests/golden/prompts.json`` holds the expected renders; regenerate it on
purpose, after a prompt change you meant, with:

    python -m tests.prompt_renders
"""
import json
from pathlib import Path

import httpx

from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.clef_e2e import process_claim_clef
from claims_pipeline.e2e import E2EOut, process_claim_e2e
from claims_pipeline.llm.anthropic_client import AnthropicClient
from claims_pipeline.llm.chat_backend import ChatBackend, ExplainOut, ExtractOut, JudgmentOut, ObservationOut, ReadOut
from claims_pipeline.llm.clef import ClefClient, ClefJudge, ClefScorer
from claims_pipeline.llm.clients import Routed
from claims_pipeline.settings import Settings
from claims_pipeline.llm.openai_client import reply_format
from claims_pipeline.llm.scripted import ScriptedBackend
from claims_pipeline.llm.types import Usage
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy

Settings.model_config["env_file"] = None   # as in conftest: a deploy's .env never changes the renders

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"
GOLDEN = Path(__file__).resolve().parent / "golden" / "prompts.json"
CLAIMS = ("01-clean-medical-cancellation", "08-altered-booking-image")


def _texts(content) -> list[str]:
    return [b["text"] if b.get("type") == "text" else f"<{b.get('type')}>" for b in content]


class _Recorder(AnthropicClient):
    def __init__(self, answers):
        self.settings, self.answers, self.calls = Settings(), answers, []

    def parse(self, component, system, content, output_model):
        self.calls.append({"component": component, "system": system, "content": _texts(content)})
        return self.answers[component], Usage(model="fake")


def _decomposed(claim: str) -> list[dict]:
    loaded = load_claim_dir(ROOT / claim)
    api = _Recorder({
        "read_document": ReadOut(doc_type="medical_report", language="en", lines=["line one"], alteration_signs=[], summary="s"),
        "extract_facts": ExtractOut(observations=[ObservationOut(
            fact="incident_date", document_id="description", line=1, quote="q", value="2025-03-20", confidence=0.9)]),
        "judge": JudgmentOut(answer="no", quote="q", rationale="r", confidence=0.9),
        "explain": ExplainOut(text="x"),
    })
    process_claim(loaded.input, get_policy(loaded.policy), ChatBackend(Settings(), api))
    return api.calls


def _e2e(claim: str) -> list[dict]:
    loaded = load_claim_dir(ROOT / claim)
    api = _Recorder({"e2e": E2EOut(outcome="UNCERTAIN", confidence=0.5, summary="s", reasoning="r")})
    process_claim_e2e(loaded.input, get_policy(loaded.policy), api)
    return api.calls


def _clef(claim: str) -> list[dict]:
    """Clef as judge, as scorer, and end to end: every request body, images replaced."""
    loaded = load_claim_dir(ROOT / claim)
    policy = get_policy(loaded.policy)
    oracle = json.loads(loaded.oracle_path.read_text())
    bodies: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        body["images"] = [i.split(";")[0] for i in body.get("images", [])]
        bodies.append(body)
        answers = {name: {"choice": next(iter(q["criteria"])), "confidence": 0.9}
                   for name, q in body["questions"].items()}
        return httpx.Response(200, json={"answers": answers})

    settings = Settings(clef_base_url="http://clef.local/v1", trace=False)
    client = ClefClient(settings, http=httpx.Client(base_url="http://clef.local/v1",
                                                    transport=httpx.MockTransport(handler)))
    scorer = ClefScorer(settings, client)
    backend = Routed(ScriptedBackend(oracle), judge=ClefJudge(settings, client),
                     score_document=scorer, score_facts=scorer)
    process_claim(loaded.input, policy, backend)
    process_claim_clef(loaded.input, policy, client)
    return bodies


def render_all() -> dict:
    out = {}
    for claim in CLAIMS:
        out[f"decomposed/{claim}"] = _decomposed(claim)
        out[f"e2e/{claim}"] = _e2e(claim)
        out[f"clef/{claim}"] = _clef(claim)
    for model in (ReadOut, ExtractOut, JudgmentOut, ExplainOut, E2EOut):
        out[f"reply_format/{model.__name__}"] = reply_format(model)
    return out


if __name__ == "__main__":
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(json.dumps(render_all(), indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {GOLDEN}")
