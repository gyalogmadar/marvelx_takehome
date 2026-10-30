"""Every model call is logged in full: request, raw response, reasoning, parsed output,
usage, errors; images as blobs; the claim scope; both clients."""
import base64
import json
from pathlib import Path

import httpx
import pytest

from claims_pipeline import prompts
from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.llm import calllog
from claims_pipeline.llm.anthropic_backend import AnthropicBackend, AnthropicClient, JudgmentOut
from claims_pipeline.llm.base import BackendError
from claims_pipeline.llm.calllog import CallLog, claim_scope
from claims_pipeline.llm.config import Settings
from claims_pipeline.llm.openai_backend import OpenAIClient
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"
PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def openai_client(tmp_path, handler, **kw) -> OpenAIClient:
    settings = Settings(backend="openai", openai_base_url="http://llm.local/v1", model="qwen-test",
                        trace_dir=str(tmp_path), **kw)
    http = httpx.Client(base_url="http://llm.local/v1", transport=httpx.MockTransport(handler))
    return OpenAIClient(settings, http=http)


def test_openai_call_is_logged_in_full_with_reasoning_and_blob(tmp_path):
    def handler(request):
        return httpx.Response(200, json={
            "model": "qwen-test",
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"answer": "yes", "rationale": "seen", "confidence": 0.7}),
                "reasoning_content": "The certificate says admitted on the 20th, so yes."}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300,
                      "completion_tokens_details": {"reasoning_tokens": 240}}})

    client = openai_client(tmp_path, handler, openai_disable_thinking=False)
    content = [{"type": "text", "text": "Is it covered?"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(PNG).decode()}}]
    with claim_scope("claim 7", "decomposed"):
        out, usage = client.parse("judge", "SYSTEM PROMPT", content, JudgmentOut)
    assert out.answer == "yes" and usage.reasoning_tokens == 240 and usage.call_id
    (rec,) = calllog.read(tmp_path)
    assert rec["call_id"] == usage.call_id
    assert (rec["claim_id"], rec["strategy"], rec["component"], rec["provider"]) == (
        "claim 7", "decomposed", "judge", "openai")
    assert rec["endpoint"] == "http://llm.local/v1/chat/completions"
    req = rec["request"]
    assert req["messages"][0]["content"].startswith("SYSTEM PROMPT\n\n# Reply format")
    assert req["response_format"]["json_schema"]["name"] == "JudgmentOut"
    assert req["chat_template_kwargs"] == {"enable_thinking": True}  # thinking on for judge
    img = req["messages"][1]["content"][1]["image_url"]
    assert img["bytes"] == len(PNG) and img["media_type"] == "image/png"
    assert (tmp_path / img["blob"]).read_bytes() == PNG             # byte for byte
    assert rec["response"]["choices"][0]["message"]["reasoning_content"].startswith("The certificate")
    assert rec["reasoning"] == "The certificate says admitted on the 20th, so yes."
    assert rec["parsed"] == {"answer": "yes", "document_filename": None, "line": None, "quote": "",
                             "rationale": "seen", "confidence": 0.7, "review": None}
    assert rec["usage"]["reasoning_tokens"] == 240 and rec["error"] is None and rec["latency_ms"] >= 0


def test_inline_think_and_failed_calls_are_logged(tmp_path):
    reply = '<think>weighing the dates</think>{"answer": "maybe", "rationale": "", "confidence": 1}'
    client = openai_client(tmp_path, lambda r: httpx.Response(200, json={
        "model": "qwen-test", "choices": [{"finish_reason": "stop", "message": {"content": reply}}]}))
    with pytest.raises(BackendError, match="does not match"):
        client.parse("judge", "S", [{"type": "text", "text": "x"}], JudgmentOut)
    client = openai_client(tmp_path, lambda r: httpx.Response(503, text="model loading"))
    with pytest.raises(BackendError, match="503"):
        client.parse("explain", "S", [{"type": "text", "text": "x"}], JudgmentOut)
    bad, down = calllog.read(tmp_path)
    assert bad["reasoning"] == "weighing the dates" and bad["output_text"] == reply
    assert bad["parsed"] is None and "does not match" in bad["error"]
    assert down["component"] == "explain" and "503" in down["error"]
    assert down["response"] == {"status": 503, "text": "model loading"}
    assert calllog.read(tmp_path, component="explain") == [down]


def test_anthropic_call_is_logged_in_full(tmp_path):
    import anthropic
    import httpx2

    def handler(request):
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "stop_reason": "end_turn", "stop_sequence": None,
            "content": [{"type": "thinking", "thinking": "Summary: the dates line up.", "signature": "sig"},
                        {"type": "text", "text": json.dumps({"answer": "no", "rationale": "r", "confidence": 0.9})}],
            "usage": {"input_tokens": 200, "output_tokens": 50, "cache_read_input_tokens": 1000,
                      "cache_creation_input_tokens": 0}})

    sdk = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    client = AnthropicClient(Settings(trace_dir=str(tmp_path)), sdk)
    with claim_scope("c1", "e2e"):
        out, usage = client.parse("e2e", "POLICY", [{"type": "text", "text": "claim"}], JudgmentOut)
    (rec,) = calllog.read(tmp_path)
    assert usage.call_id == rec["call_id"] and rec["provider"] == "anthropic"
    assert rec["request"]["system"][0]["text"] == "POLICY"
    assert rec["request"]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert rec["request"]["output_format"]["name"] == "JudgmentOut"
    assert rec["reasoning"] == "Summary: the dates line up."
    assert rec["response"]["usage"]["cache_read_input_tokens"] == 1000
    assert rec["parsed"]["answer"] == "no" and rec["endpoint"].endswith("/v1/messages")


def test_pipeline_links_every_model_call_to_its_log_line(tmp_path):
    loaded = load_claim_dir(ROOT / "10-voluntary-change")

    def handler(request):
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        payload = {
            "ReadOut": {"doc_type": "booking_confirmation", "language": "en", "summary": "booking"},
            "ExtractOut": {"observations": []},
            "JudgmentOut": {"answer": "no", "rationale": "voluntary", "confidence": 0.9},
        }.get(name, {"text": "Dear Emma"})
        return httpx.Response(200, json={"model": "qwen-test",
                                         "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]})

    client = openai_client(tmp_path, handler)
    result = process_claim(loaded.input, get_policy("cfsr"), AnthropicBackend(client.settings, client))
    log = {r["call_id"]: r for r in calllog.read(tmp_path, claim_id=loaded.input.claim_id)}
    assert len(log) == len(result.calls) and all(c.call_id in log for c in result.calls)
    assert {r["strategy"] for r in log.values()} == {"decomposed"}
    assert {r["component"] for r in log.values()} == {"read_document", "extract_facts", "judge", "explain"}
    # every call names the prompt versions it was built from, and those are the files' current texts
    current = {name: p.id for name, p in prompts.all_prompts().items()}
    for rec in log.values():
        used = {u["name"]: u["id"] for u in rec["prompts"]}
        assert {"role", "submission_stamp", "system", rec["component"], "reply_format"} <= set(used), rec["component"]
        assert all(current[name] == pid for name, pid in used.items())


def test_cli_prints_the_log(tmp_path, capsys):
    log = CallLog(tmp_path)
    with claim_scope("c9", "decomposed"):
        log.record(component="judge", provider="openai", endpoint="e", model="m",
                   request={"messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q?"}]},
                   output_text='{"answer":"no"}', reasoning="because", usage={"input_tokens": 3})
    assert calllog.main([str(tmp_path), "--claim", "c9"]) == 0
    out = capsys.readouterr().out
    assert "c9 judge m" in out and "SYS" in out and "Q?" in out and "because" in out and '{"answer":"no"}' in out
