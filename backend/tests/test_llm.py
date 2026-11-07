"""The Claude adapter, against a stand-in client: JSON parsing, retries, refusals, rate limits."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.core.llm import ClaudeLLM, LLMError, RateLimited


def _reply(text, stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop, stop_details=None, model=model,
        usage=SimpleNamespace(input_tokens=100, cache_read_input_tokens=50, cache_creation_input_tokens=0,
                              output_tokens=20))


def _claude(*replies):
    llm = ClaudeLLM.__new__(ClaudeLLM)
    llm._anthropic, llm.default_model, llm.effort = anthropic, "claude-opus-5-5", "medium"
    queue = list(replies)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return llm, calls


def test_reads_json_even_inside_a_code_fence():
    llm, calls = _claude(_reply('Here it is:\n```json\n{"issues": []}\n```'))
    result = llm.complete_json("You check claims.", "{}")
    assert result.data == {"issues": []} and result.input_tokens == 150 and result.output_tokens == 20
    request = calls[0]
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["output_config"] == {"effort": "medium"} and "thinking" not in request
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_retries_once_when_the_reply_is_not_an_object():
    llm, calls = _claude(_reply('[{"code": "M17.11"}]'), _reply('{"diagnosis_codes": []}'))
    assert llm.complete_json("s", "u").data == {"diagnosis_codes": []} and len(calls) == 2
    llm, _ = _claude(_reply("no json"), _reply('{"cut": '  , stop="max_tokens"))
    with pytest.raises(LLMError, match="cut off"):
        llm.complete_json("s", "u")


def test_a_refusal_is_an_error_not_an_empty_answer():
    refused = _reply("")
    refused.stop_reason, refused.stop_details = "refusal", SimpleNamespace(category="cyber")
    llm, _ = _claude(refused)
    with pytest.raises(LLMError, match="declined"):
        llm.complete_json("s", "u")


def test_rate_limit_becomes_a_wait():
    response = httpx2.Response(429, headers={"retry-after": "30"}, request=httpx2.Request("POST", "https://x"))
    llm, _ = _claude(anthropic.RateLimitError("slow down", response=response, body=None))
    with pytest.raises(RateLimited) as exc:
        llm.complete_json("s", "u")
    assert exc.value.wait_seconds == 31
