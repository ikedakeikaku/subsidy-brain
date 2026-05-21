"""Claude API client — async, cache-aware, structured-output first.

2026-05 best practices applied:

  * **Lazy client initialization** — no API key required at import time, so the
    public demo and the test suite can import freely.
  * **Prompt caching** via ``cache_control: {"type": "ephemeral"}`` so that
    long system prompts (guideline rules, scoring rubrics) bill at 1/10 after
    the first hit.
  * **Structured output via tool_use** — every JSON response is grounded in a
    Pydantic-derived JSON Schema so business logic can validate it before the
    LLM output ever touches the downstream UI / Word writer.
  * **Token usage exposure** — ``last_usage`` is updated on every call so the
    skill store can log cost and the orchestrator can budget retries.
  * Retry on the right errors: rate limit, connection, overload, internal.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Literal

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings

logger = logging.getLogger(__name__)

EffortLevel = Literal["low", "medium", "high"]

_client: anthropic.AsyncAnthropic | None = None
last_usage: dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}


def _get_client() -> anthropic.AsyncAnthropic:
    """Return a process-wide async Anthropic client; create on first use."""
    global _client
    if _client is None:
        api_key = settings.anthropic_api_key or None
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


_RETRY_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
)
# Newer SDKs expose more granular transient errors; pick them up if available.
for _name in ("InternalServerError", "OverloadedError", "APIStatusError"):
    if hasattr(anthropic, _name):
        _RETRY_ERRORS = _RETRY_ERRORS + (getattr(anthropic, _name),)


_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(_RETRY_ERRORS),
    reraise=True,
)


def _system_block(system_prompt: str, cache: bool) -> Any:
    """Render the system prompt either as plain string or as a cached block."""
    if not cache or not system_prompt:
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _update_usage(response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    last_usage.update(
        {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(
                usage, "cache_creation_input_tokens", 0
            )
            or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0)
            or 0,
        }
    )


@_RETRY_DECORATOR
async def call_claude(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    effort: EffortLevel | None = None,
    cache_system: bool = False,
) -> str:
    """Plain text generation. Set ``cache_system=True`` to cache the prompt."""
    kwargs: dict = {
        "model": model or settings.default_model,
        "max_tokens": max_tokens or settings.default_max_tokens,
        "temperature": (
            temperature if temperature is not None else settings.default_temperature
        ),
        "system": _system_block(system_prompt, cache_system),
        "messages": [{"role": "user", "content": user_message}],
    }
    if effort is not None:
        kwargs["metadata"] = {"effort": effort}
    response = await _get_client().messages.create(**kwargs)
    _update_usage(response)
    return response.content[0].text


@_RETRY_DECORATOR
async def call_claude_vision(
    system_prompt: str,
    image_data: bytes,
    media_type: str,
    text_prompt: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    effort: EffortLevel | None = None,
    cache_system: bool = False,
) -> str:
    """Send image / PDF bytes plus a text instruction."""
    b64_data = base64.standard_b64encode(image_data).decode("utf-8")
    content_blocks: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64_data,
            },
        },
        {"type": "text", "text": text_prompt},
    ]
    kwargs: dict = {
        "model": model or settings.default_model,
        "max_tokens": max_tokens or settings.default_max_tokens,
        "temperature": (
            temperature if temperature is not None else settings.default_temperature
        ),
        "system": _system_block(system_prompt, cache_system),
        "messages": [{"role": "user", "content": content_blocks}],
    }
    if effort is not None:
        kwargs["metadata"] = {"effort": effort}
    response = await _get_client().messages.create(**kwargs)
    _update_usage(response)
    return response.content[0].text


@_RETRY_DECORATOR
async def call_claude_json(
    system_prompt: str,
    user_message: str,
    json_schema: dict,
    tool_name: str = "structured_output",
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    effort: EffortLevel | None = None,
    cache_system: bool = False,
) -> dict:
    """Structured JSON output via tool_use.

    The schema is enforced by Claude's tool input validation, so callers can
    treat the return value as already conformant to ``json_schema``. The
    fallback to text-mode JSON extraction is retained for resilience.
    """
    tools = [
        {
            "name": tool_name,
            "description": (
                "構造化されたJSONデータを出力する。必ずこのツールを使用して回答すること。"
            ),
            "input_schema": json_schema,
        }
    ]
    kwargs: dict = {
        "model": model or settings.default_model,
        "max_tokens": max_tokens or settings.default_max_tokens,
        "temperature": (
            temperature if temperature is not None else settings.default_temperature
        ),
        "system": _system_block(system_prompt, cache_system),
        "messages": [{"role": "user", "content": user_message}],
        "tools": tools,
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    if effort is not None:
        kwargs["metadata"] = {"effort": effort}

    response = await _get_client().messages.create(**kwargs)
    _update_usage(response)

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input

    logger.warning("tool_use block not found; falling back to text JSON extraction.")
    for block in response.content:
        if hasattr(block, "text"):
            return parse_json_response(block.text)

    raise ValueError("構造化出力の取得に失敗しました")


def parse_json_response(response: str) -> dict:
    """Best-effort JSON extraction from a free-form text response.

    Order of attempts:
      1. ```json ... ``` fenced block
      2. outermost ``{ ... }``
      3. ``json.loads`` on the whole string
    """
    code_block_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError:
            logger.debug("JSON code block parse failed; trying next strategy.")

    first_brace = response.find("{")
    last_brace = response.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = response[first_brace : last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            logger.debug("Outermost-brace parse failed; trying whole-string.")

    try:
        return json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"応答からJSONを抽出できませんでした: {response[:200]}..."
        ) from exc


__all__ = [
    "call_claude",
    "call_claude_json",
    "call_claude_vision",
    "parse_json_response",
    "last_usage",
    "EffortLevel",
]
