"""The OpenAI-compatible client with a mock transport: request shape, structured output,
think-tag stripping, and the whole decomposed pipeline through it."""
import json
from pathlib import Path

import httpx
import pytest

from claims_pipeline.benchmark.loader import load_claim_dir
from claims_pipeline.domain import Outcome
from claims_pipeline.llm.anthropic_backend import AnthropicBackend, JudgmentOut
from claims_pipeline.llm.base import BackendError
from claims_pipeline.llm.clients import make_client, make_decomposed_backend
from claims_pipeline.llm.config import Settings
from claims_pipeline.llm.openai_backend import OpenAIClient, extract_json, to_openai_content
from claims_pipeline.pipeline import process_claim
from claims_pipeline.policies import get_policy

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"
SETTINGS = Settings(backend="openai", openai_base_url="http://llm.local/v1", model="qwen-test",
                    openai_api_key="tok")


def client_with(handler) -> OpenAIClient:
    http = httpx.Client(base_url="http://llm.local/v1", transport=httpx.MockTransport(handler),
                        headers={"Authorization": "Bearer tok"})
    return OpenAIClient(SETTINGS, http=http)


def reply(payload: dict, finish="stop", usage=None):
    return httpx.Response(200, json={
        "model": "qwen-test", "choices": [{"message": {"content": payload if isinstance(payload, str) else json.dumps(payload)},
                                          "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 900, "completion_tokens": 40,
                           "prompt_tokens_details": {"cached_tokens": 800}}})


def test_parse_sends_schema_and_returns_typed_output():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url); seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return reply({"answer": "no", "rationale": "nothing before purchase", "confidence": 0.8})

    out, usage = client_with(handler).parse("judge", "SYSTEM", [{"type": "text", "text": "Q?"}], JudgmentOut)
    assert out.answer == "no" and out.confidence == 0.8
    assert (usage.model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == ("qwen-test", 900, 40, 800)
    b = seen["body"]
    assert seen["url"].endswith("/v1/chat/completions") and seen["auth"] == "Bearer tok"
    assert b["model"] == "qwen-test" and b["temperature"] == 0.0
    assert b["messages"][0]["content"].startswith("SYSTEM\n\n# Reply format")
    assert b["messages"][1]["content"] == [{"type": "text", "text": "Q?"}]
    assert b["response_format"]["type"] == "json_schema"
    assert b["response_format"]["json_schema"]["name"] == "JudgmentOut"
    assert "answer" in b["response_format"]["json_schema"]["schema"]["properties"]
    assert b["chat_template_kwargs"] == {"enable_thinking": False}


def test_images_become_data_urls_and_think_blocks_are_stripped():
    parts = to_openai_content([{"type": "text", "text": "t"},
                               {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}])
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert extract_json('<think>\nhmm\n</think>\n{"a": 1}') == {"a": 1}
    assert extract_json('Sure, here it is:\n{"a": 1}\nDone.') == {"a": 1}
    with pytest.raises(BackendError):
        extract_json("no json here")


def test_errors_are_backend_errors():
    assert isinstance(client_with(lambda r: httpx.Response(500, text="boom")), OpenAIClient)
    with pytest.raises(BackendError, match="API error 500"):
        client_with(lambda r: httpx.Response(500, text="boom")).parse("judge", "S", [{"type": "text", "text": "x"}], JudgmentOut)
    with pytest.raises(BackendError, match="truncated"):
        client_with(lambda r: reply({"answer": "no", "rationale": "", "confidence": 1}, finish="length")).parse(
            "judge", "S", [{"type": "text", "text": "x"}], JudgmentOut)
    with pytest.raises(BackendError, match="does not match"):
        client_with(lambda r: reply({"answer": "maybe", "rationale": "", "confidence": 1})).parse(
            "judge", "S", [{"type": "text", "text": "x"}], JudgmentOut)


def test_factory_and_pipeline_through_the_openai_client():
    assert make_client(SETTINGS).name == "openai"
    assert make_client(Settings(backend="anthropic")).name == "anthropic"       # constructs without a key
    with pytest.raises(BackendError):
        make_client(Settings(backend="scripted"))

    loaded = load_claim_dir(ROOT / "10-voluntary-change")
    policy = get_policy(loaded.policy)

    def handler(request):
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        if name == "ReadOut":
            return reply({"doc_type": "booking_confirmation", "language": "en", "issuer": "EuroTrips",
                          "lines": None, "alteration_signs": [], "summary": "booking"})
        if name == "ExtractOut":
            return reply({"observations": [
                {"fact": "incident_date", "document_id": "description", "line": 1, "quote": "15 March", "value": "2025-03-15", "confidence": 0.7},
                {"fact": "policy_period", "document_id": "d1-booking-confirmation", "line": 9,
                 "quote": "CFSR cover period: 2025-02-01 to 2025-04-10", "value": "2025-02-01/2025-04-10", "confidence": 0.9},
                {"fact": "purchase_date", "document_id": "d1-booking-confirmation", "line": 8, "quote": "Booked", "value": "2025-02-01", "confidence": 0.9}]})
        if name == "JudgmentOut":
            return reply({"answer": "no", "document_filename": "description", "line": 1, "quote": "my friend",
                          "rationale": "a voluntary change", "confidence": 0.9})
        return reply({"text": "Dear Emma, ..."})

    backend = AnthropicBackend(SETTINGS, client_with(handler))
    result = process_claim(loaded.input, policy, backend)
    assert result.decision.outcome == Outcome.DENY and "not covered" in result.decision.summary
    assert {c.model for c in result.calls} == {"qwen-test"} and all(c.ok for c in result.calls)
    assert backend.name == "openai"
    assert isinstance(make_decomposed_backend(SETTINGS), AnthropicBackend)


def test_images_reach_a_local_server_as_png_or_jpeg_whatever_their_extension():
    import base64, io
    from PIL import Image
    from claims_pipeline.llm.anthropic_backend import image_block
    from claims_pipeline.llm.images import sniff

    def encoded(fmt):
        buf = io.BytesIO(); Image.new("RGB", (8, 8), "white").save(buf, format=fmt); return buf.getvalue()

    webp, jpeg = encoded("WEBP"), encoded("JPEG")
    assert (sniff(webp), sniff(jpeg)) == ("image/webp", "image/jpeg")
    # a real WebP is converted to PNG; a JPEG named .webp is sent as the JPEG it is
    url = to_openai_content([image_block(webp, "image/webp")])[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,") and sniff(base64.b64decode(url.split(",", 1)[1])) == "image/png"
    block = image_block(jpeg, "image/webp")
    assert block["source"]["media_type"] == "image/jpeg"
    assert to_openai_content([block])[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_judges_are_not_shown_our_trust_conclusions():
    from claims_pipeline.domain import Reading, Trust
    from claims_pipeline.llm.anthropic_backend import render_readings

    doc = Reading(id="d1", filename="cert.md", kind="markdown", lines=["x"], trust=Trust.QUESTIONABLE,
                   trust_reasons=["claimant_name disagrees with another document"], alteration_signs=["overwritten date"])
    text = render_readings([doc])
    assert "disagrees" not in text and "trust" not in text and "overwritten date" in text
