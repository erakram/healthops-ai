"""A thin LLM adapter: agents ask for JSON, the adapter handles the provider, retries,
and records tokens and latency for every call.

Agents depend on the LLM protocol, not on Groq, so tests pass in a fake client and a
second provider is one more class here.
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


def default_llm() -> LLM | None:
    if not settings.groq_api_key:
        logger.warning("GROQ_API_KEY is not set: uploaded claims can't be processed")
        return None
    return GroqLLM(settings.groq_api_key, settings.extraction_model, settings.request_timeout, settings.max_retries)
