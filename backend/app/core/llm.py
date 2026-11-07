"""A thin LLM adapter: agents ask for JSON, the adapter handles the provider, retries,
and records tokens and latency for every call.

Agents depend on the LLM protocol, not on a provider, so tests pass in a fake client.
Two providers: Claude (ClaudeLLM, the default when ANTHROPIC_API_KEY is set) and Groq
(GroqLLM). LLM_PROVIDER picks one explicitly.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class RateLimited(LLMError):
    """The provider's per-minute budget is used up. The job waits instead of failing."""

    def __init__(self, message: str, wait_seconds: float):
        super().__init__(message)
        self.wait_seconds = wait_seconds


_RETRY_IN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s")


def _wait_from(message: str) -> float:
    match = _RETRY_IN.search(message)
    if not match:
        return 20.0
    return int(match.group(1) or 0) * 60 + float(match.group(2)) + 1.0


@dataclass
class LLMResult:
    data: dict
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLM(Protocol):
    def complete_json(self, system: str, user: str, model: str | None = None) -> LLMResult: ...


def _model_options(model: str) -> dict:
    # gpt-oss models reason before answering; low effort keeps latency and free-tier usage down.
    return {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}


class GroqLLM:
    def __init__(self, api_key: str, default_model: str, timeout: float, max_retries: int):
        from groq import Groq, RateLimitError

        self.client = Groq(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._rate_limit_error = RateLimitError
        self.default_model = default_model

    def complete_json(self, system: str, user: str, model: str | None = None) -> LLMResult:
        model = model or self.default_model
        started = time.perf_counter()
        last_error = None
        for _ in range(2):  # one extra try if the model returns invalid JSON
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0,
                    **_model_options(model),
                )
            except self._rate_limit_error as exc:  # the SDK already retried a few times
                raise RateLimited(f"Rate limited by {model}", _wait_from(str(exc))) from exc
            content = response.choices[0].message.content or ""
            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if not isinstance(data, dict):  # seen in evals: a bare list instead of the requested object
                last_error = ValueError(f"expected a JSON object, got {type(data).__name__}")
                continue
            usage = response.usage
            return LLMResult(data=data, model=model,
                             input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                             output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                             latency_ms=int((time.perf_counter() - started) * 1000))
        raise LLMError(f"Model returned invalid JSON twice: {last_error}")


_JSON_ONLY = ("\n\nRespond with the JSON object only: no prose before or after it, no code fences.")


def _json_object(text: str) -> dict | None:
    """The model's JSON reply as a dict, tolerating stray prose or fences around it. A reply
    that is valid JSON but not an object (a list, say) is rejected, not mined for an object."""
    stripped = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class ClaudeLLM:
    """Claude through the Anthropic SDK. Thinking is adaptive (the model's default); effort sets
    how much it thinks. Refusal fallbacks are on: if a safety classifier declines, the API
    re-runs the request on a fallback model within the same call."""

    def __init__(self, api_key: str, default_model: str, timeout: float, max_retries: int, effort: str = "medium"):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.default_model = default_model
        self.effort = effort

    def complete_json(self, system: str, user: str, model: str | None = None) -> LLMResult:
        model = model or self.default_model
        started = time.perf_counter()
        problem = "no reply"
        for _ in range(2):  # one extra try if the reply isn't a JSON object
            try:
                response = self.client.beta.messages.create(
                    model=model,
                    max_tokens=16000,
                    system=[{"type": "text", "text": system + _JSON_ONLY, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": self.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except self._anthropic.RateLimitError as exc:  # the SDK already retried a few times
                wait = float(exc.response.headers.get("retry-after") or 20) + 1
                raise RateLimited(f"Rate limited by {model}", wait) from exc
            if response.stop_reason == "refusal":
                category = getattr(response.stop_details, "category", None) if response.stop_details else None
                raise LLMError(f"{model} declined the request ({category or 'no category'})")
            text = "".join(block.text for block in response.content if block.type == "text")
            data = _json_object(text)
            if data is None:
                problem = "reply was cut off" if response.stop_reason == "max_tokens" else "reply wasn't a JSON object"
                continue
            usage = response.usage
            return LLMResult(data=data, model=response.model,
                             input_tokens=(usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0)
                             + (usage.cache_creation_input_tokens or 0),
                             output_tokens=usage.output_tokens or 0,
                             latency_ms=int((time.perf_counter() - started) * 1000))
        raise LLMError(f"Model's {problem} twice")


def make_llm(model: str | None = None) -> LLM | None:
    """The configured provider's client, or None when its key isn't set."""
    key = settings.llm_api_key
    if not key:
        return None
    model = model or settings.extraction_model
    if settings.llm_provider == "anthropic":
        return ClaudeLLM(key, model, settings.request_timeout, settings.max_retries, settings.claude_effort)
    return GroqLLM(key, model, settings.request_timeout, settings.max_retries)


def default_llm() -> LLM | None:
    llm = make_llm()
    if llm is None:
        logger.warning("%s is not set: uploaded claims can't be processed", settings.llm_key_name)
    else:
        logger.info("LLM provider: %s, models %s / %s", settings.llm_provider, settings.extraction_model,
                    settings.review_model)
    return llm
