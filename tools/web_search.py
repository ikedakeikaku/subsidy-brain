"""Unified web search.

Two providers are available:

  * **anthropic** — uses Claude's built-in ``web_search_20250305`` tool.
    Preferred because Claude can search, reason, and synthesise in one
    request, returns better citations, and uses the same key as the rest
    of the system.

  * **perplexity** — kept as fallback for environments where Anthropic
    web search is unavailable or rate-limited.

The default ``web_search`` picks anthropic when ``ANTHROPIC_API_KEY`` is set
and the SDK exposes web search, else falls back to perplexity, else returns
an empty result. Callers should treat the result as a value-object, not as
a side effect, so the demo and CI paths stay deterministic without keys.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import anthropic

from config.settings import settings

logger = logging.getLogger(__name__)

Provider = Literal["anthropic", "perplexity", "none"]


@dataclass(frozen=True)
class WebSearchResult:
    """Provider-agnostic search result."""

    provider: Provider
    answer: str
    citations: list[str]
    raw: dict | None = None


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


async def search_with_anthropic(
    query: str,
    *,
    system_prompt: str = "",
    model: str | None = None,
    max_uses: int = 5,
    max_tokens: int = 4096,
) -> WebSearchResult:
    """Use Claude's built-in web_search tool.

    The tool name ``web_search_20250305`` is the GA variant. Claude may call
    it 0–``max_uses`` times during the response, returning a synthesised
    answer with citations attached.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot use anthropic provider")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    response = await client.messages.create(
        model=model or settings.default_model,
        max_tokens=max_tokens,
        system=system_prompt or (
            "あなたは日本の中小企業向け補助金の調査スペシャリストです。"
            "公式サイトの最新情報を最優先で参照し、引用元のURLを必ず明示してください。"
        ),
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
        messages=[{"role": "user", "content": query}],
    )

    answer_parts: list[str] = []
    citations: list[str] = []
    for block in response.content:
        # text blocks carry both prose and citation references
        if getattr(block, "type", None) == "text":
            answer_parts.append(getattr(block, "text", ""))
            for cite in getattr(block, "citations", []) or []:
                url = getattr(cite, "url", None) or (
                    cite.get("url") if isinstance(cite, dict) else None
                )
                if url and url not in citations:
                    citations.append(url)
        # Some SDK versions surface a separate web_search_tool_result block
        elif getattr(block, "type", None) == "web_search_tool_result":
            results = getattr(block, "content", None) or []
            for r in results:
                url = getattr(r, "url", None) or (
                    r.get("url") if isinstance(r, dict) else None
                )
                if url and url not in citations:
                    citations.append(url)

    return WebSearchResult(
        provider="anthropic",
        answer="".join(answer_parts).strip(),
        citations=citations,
        raw=None,
    )


# ---------------------------------------------------------------------------
# Perplexity fallback
# ---------------------------------------------------------------------------


async def search_with_perplexity(
    query: str,
    *,
    system_prompt: str = "",
    model: str = "sonar",
) -> WebSearchResult:
    if not settings.perplexity_api_key:
        raise RuntimeError("PERPLEXITY_API_KEY not set; cannot use perplexity provider")
    from tools.perplexity_search import _call_perplexity

    result = await _call_perplexity(
        system_prompt=system_prompt or "事実に基づいた回答を返してください。",
        user_message=query,
        model=model,
        temperature=0.1,
        max_tokens=4096,
    )
    return WebSearchResult(
        provider="perplexity",
        answer=result.content,
        citations=list(result.citations or []),
        raw=None,
    )


# ---------------------------------------------------------------------------
# Auto-pick
# ---------------------------------------------------------------------------


async def web_search(
    query: str,
    *,
    system_prompt: str = "",
    prefer: Provider | None = None,
) -> WebSearchResult:
    """Pick the best available provider and search.

    Provider preference order:
      1. ``prefer`` argument (if given and key available)
      2. anthropic (if ANTHROPIC_API_KEY set)
      3. perplexity (if PERPLEXITY_API_KEY set)
      4. empty no-op result
    """
    order: list[Provider] = []
    if prefer == "anthropic":
        order.append("anthropic")
    elif prefer == "perplexity":
        order.append("perplexity")
    if "anthropic" not in order:
        order.append("anthropic")
    if "perplexity" not in order:
        order.append("perplexity")

    for provider in order:
        try:
            if provider == "anthropic":
                return await search_with_anthropic(query, system_prompt=system_prompt)
            if provider == "perplexity":
                return await search_with_perplexity(query, system_prompt=system_prompt)
        except RuntimeError as e:
            logger.info("web_search: %s skipped: %s", provider, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("web_search: %s failed: %s", provider, e)

    return WebSearchResult(provider="none", answer="", citations=[], raw=None)
