"""Failed model calls: the prompt shows the schema, sampling follows thinking, and every
kind of failure degrades to missing information instead of losing the claim."""
import json
from pathlib import Path

import httpx
import pytest

from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.domain import Outcome, Verdict
from claims_pipeline.e2e import process_claim_e2e
from claims_pipeline.llm.chat_backend import ExtractOut, JudgmentOut
from claims_pipeline.llm.base import BackendError
from claims_pipeline.settings import Settings
from claims_pipeline.llm.openai_client import OpenAIClient, example_of, reply_format
from claims_pipeline.llm.scripted import OracleMissing, ScriptedBackend
from claims_pipeline.llm.types import Usage
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


def openai_client(handler, **kw) -> OpenAIClient:
    settings = Settings(backend="openai", openai_base_url="http://llm.local/v1", model="qwen-test", **kw)
    return OpenAIClient(settings, http=httpx.Client(base_url="http://llm.local/v1",
                                                    transport=httpx.MockTransport(handler)))


def ok_reply(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"model": "qwen-test", "choices": [
        {"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]})


# -- 1. the schema reaches the prompt ---------------------------------------------------
def test_reply_format_shows_schema_and_shape():
    text = reply_format(ExtractOut)
    assert text.startswith("# Reply format") and '"observations"' in text and '"title"' not in text
    shape = example_of(ExtractOut.model_json_schema())
    assert list(shape) == ["observations"] and len(shape["observations"]) == 1
    assert set(shape["observations"][0]) == {"fact", "document_id", "line", "quote", "value", "confidence", "review"}
    assert example_of(JudgmentOut.model_json_schema())["answer"] == "yes|no|unknown"


def test_system_prompt_carries_the_reply_format():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return ok_reply({"answer": "no", "rationale": "r", "confidence": 0.9})

    openai_client(handler).parse("judge", "TASK", [{"type": "text", "text": "Q"}], JudgmentOut)
    system = seen["messages"][0]["content"]
    assert system.startswith("TASK\n\n# Reply format") and '"rationale"' in system


# -- 2. thinking, sampling and budget per component --------------------------------------
@pytest.mark.parametrize("component,thinking,temperature", [
    ("read_document", False, 0.0), ("extract_facts", True, 0.6), ("judge", True, 0.6), ("explain", False, 0.0)])
def test_thinking_and_sampling_per_component(component, thinking, temperature):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return ok_reply({"answer": "no", "rationale": "r", "confidence": 0.9})

    client = openai_client(handler, openai_disable_thinking=False, max_tokens_extract_facts=32000)
    client.parse(component, "S", [{"type": "text", "text": "Q"}], JudgmentOut)
    assert seen["chat_template_kwargs"] == {"enable_thinking": thinking}
    assert seen["temperature"] == temperature
    assert ("top_k" in seen) == thinking and seen.get("top_p", 0.95) == 0.95
    assert seen["max_tokens"] == (32000 if component == "extract_facts" else 16000)


def test_master_switch_turns_thinking_off_everywhere():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return ok_reply({"answer": "no", "rationale": "r", "confidence": 0.9})

    openai_client(handler).parse("judge", "S", [{"type": "text", "text": "Q"}], JudgmentOut)
    assert seen["chat_template_kwargs"] == {"enable_thinking": False} and seen["temperature"] == 0.0


def test_failed_call_carries_its_own_usage_and_call_id(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"model": "qwen-test", "usage": {"prompt_tokens": 1500, "completion_tokens": 16000},
                                         "choices": [{"finish_reason": "length", "message": {"content": "{"}}]})

    client = openai_client(handler, trace_dir=str(tmp_path))
    with pytest.raises(BackendError, match="truncated") as info:
        client.parse("extract_facts", "S", [{"type": "text", "text": "Q"}], ExtractOut)
    usage = info.value.usage
    assert (usage.output_tokens, usage.input_tokens) == (16000, 1500)
    logged = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert logged[-1]["call_id"] == usage.call_id and logged[-1]["error"].startswith("response truncated")


# -- 3. failures degrade to missing information -------------------------------------------
class Failing:
    """A scripted backend whose calls to some components fail like a model call would."""

    def __init__(self, inner: ScriptedBackend, fail: set[str]):
        self.inner, self.fail, self.name, self.last_usage = inner, fail, inner.name, None

    def __getattr__(self, component):
        fn = getattr(self.inner, component)

        def call(req):
            if component in self.fail:
                exc = BackendError(f"{component}: response truncated at max_tokens")
                exc.usage = Usage(model="qwen-test", output_tokens=16000, call_id=f"call-{component}")
                raise exc
            out = fn(req)
            self.last_usage = self.inner.last_usage
            return out
        return call


def run(claim: str, fail: set[str]):
    loaded = load_claim_dir(ROOT / claim)
    backend = Failing(ScriptedBackend.from_file(loaded.oracle_path), fail)
    return process_claim(loaded.input, get_policy(loaded.policy), backend)


def test_failed_extraction_refers_the_claim_instead_of_losing_it():
    result = run("01-clean-medical-cancellation", {"extract_facts"})
    assert result.decision.outcome == Outcome.UNCERTAIN and result.decision.payout is None
    assert all(not f.known for f in result.facts if f.name != "claim_submitted")
    assert "extract_facts: " in result.decision.unknowns[0] and "failed" in result.decision.unknowns[0]
    failed = [c for c in result.calls if not c.ok]
    assert [(c.component, c.call_id, c.output_tokens) for c in failed] == [("extract_facts", "call-extract_facts", 16000)]


def test_failed_read_marks_the_document_unreadable_and_blocks_approval():
    result = run("01-clean-medical-cancellation", {"read_document"})
    assert result.decision.outcome == Outcome.UNCERTAIN and result.decision.payout is None
    read = [d for d in result.readings if d.kind not in ("description", "metadata")]
    assert read and all(d.doc_type == "unreadable" and "model call failed" in d.summary for d in read)
    assert any(d.lines for d in read if d.kind != "image")             # text files keep their lines


def test_failed_judgment_is_an_unknown_check():
    result = run("01-clean-medical-cancellation", {"judge"})
    judged = [r for r in result.exclusions + [x for c in result.coverages for x in (c.match, *c.checks)]
              if r.kind == "judged"]
    assert judged and all(r.verdict == Verdict.UNKNOWN and "model call failed" in r.rationale for r in judged)
    assert result.decision.outcome == Outcome.UNCERTAIN


def test_failed_explanation_keeps_the_decision():
    result = run("01-clean-medical-cancellation", {"explain"})
    assert result.decision.outcome == Outcome.APPROVE and result.decision.payout == pytest.approx(1240.0)
    assert result.decision.explanation is None and not result.decision.unknowns      # narration runs after the decision
    assert [c.component for c in result.calls if not c.ok] == ["explain"]


def test_a_failed_read_does_not_soften_a_denial():
    result = run("02-incident-before-policy-start", {"read_document"})
    assert result.decision.outcome == Outcome.DENY                       # the dates were still read and fail
    assert "read_document: " in result.decision.unknowns[0]


def test_oracle_gaps_still_fail_loudly():
    loaded = load_claim_dir(ROOT / "01-clean-medical-cancellation")
    with pytest.raises(OracleMissing):
        process_claim(loaded.input, get_policy(loaded.policy), ScriptedBackend({"documents": {}}))


def test_failed_e2e_call_is_an_uncertain_result():
    loaded = load_claim_dir(ROOT / "01-clean-medical-cancellation")
    client = openai_client(lambda r: httpx.Response(503, text="model loading"))
    result = process_claim_e2e(loaded.input, get_policy(loaded.policy), client)
    assert result.decision.outcome == Outcome.UNCERTAIN and "503" in result.decision.unknowns[0]
    assert [(c.component, c.ok) for c in result.calls] == [("e2e", False)] and result.calls[0].call_id


def test_local_qwen_is_priced_whatever_name_the_server_reports():
    from claims_pipeline.llm.pricing import cost_usd

    for name in ("Qwen3.6-27B-UD-Q4_K_XL.gguf", "/models/qwen3.8-27b-hauhau-aggressive"):
        # 1M input of which 200k cached, 100k output: 0.8 x 0.25 + 0.2 x 0.04 + 0.1 x 2.09
        assert cost_usd(name, 1_000_000, 100_000, 200_000) == round(0.2 + 0.008 + 0.209, 6)
    assert cost_usd("qwen-test", 1_000_000, 0, 0) == 0.0
