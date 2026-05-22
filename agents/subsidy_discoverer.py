"""Discover a subsidy program's official URLs from a natural-language name.

The user says "持続化補助金 第19回". The system needs:

  * official landing URL
  * guideline PDF URL
  * official 様式 docx / xlsx URLs
  * application deadline
  * issuing body

The registry can be hand-curated, but for a fresh subsidy or for users who
don't want to maintain YAML, the discoverer runs a web search round
(Anthropic-preferred, Perplexity fallback) and returns a ``SubsidyProgram``
draft that can be inserted into a registry or used in-flight.

Without any web-search credential the discoverer is a no-op (logs a
warning, returns ``None``).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from config.settings import settings
from schemas.subsidy_registry import SubsidyForm, SubsidyProgram
from tools.web_search import web_search

logger = logging.getLogger(__name__)


_DISCOVERY_SYSTEM = """\
あなたは日本の中小企業向け補助金の調査スペシャリストです。
ユーザーが指定した補助金の公式情報を、以下のJSON形式で返してください。

出力ルール:
- 公式サイト（中小企業庁・経産省・全国商工会連合会・全国商工会議所連合会 等）の情報を優先する
- 二次情報サイト・代行業者サイトは引用しない
- 情報が確認できない項目は空文字 "" を返す
- 全てのURLは https://〜 で始まる完全な形で記載する
- 様式 (form) は判明している限り全て列挙

期待するJSONスキーマ:
{
  "program_id":          str,
  "canonical_name":      str,
  "round_number":        int,
  "issuing_body":        str,
  "landing_url":         str,
  "guideline_pdf_url":   str,
  "application_deadline":str,
  "max_award_yen":       int,
  "subsidy_rate":        float,
  "forms": [
    {"form_id": "様式1", "name": "申請書", "url": "...", "ext": "docx|xlsx|pdf"},
    ...
  ],
  "keywords_for_research": [str, str, ...]
}

JSON以外の前置きや解説は一切出力せず、JSONオブジェクトのみを返してください。
"""


async def discover_subsidy(query: str) -> SubsidyProgram | None:
    """Return a SubsidyProgram draft, or None if no web-search credential.

    Caller should review the result (URLs in particular) before saving it
    to the persistent registry — both web-search providers can hallucinate
    URLs.
    """
    if not (settings.anthropic_api_key or settings.perplexity_api_key):
        logger.info(
            "SubsidyDiscoverer: no web-search credential set; cannot discover %r",
            query,
        )
        return None

    result = await web_search(
        f"対象補助金: {query}\n\n"
        "上記の補助金について、公式URLと様式情報を可能な限り正確に調査してください。",
        system_prompt=_DISCOVERY_SYSTEM,
    )
    if not result.answer:
        logger.warning("SubsidyDiscoverer: empty answer from %s", result.provider)
        return None

    payload = _extract_json(result.answer)
    if not payload:
        logger.warning("SubsidyDiscoverer: no JSON in response")
        return None

    return _to_subsidy_program(payload)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction (LLMs sometimes wrap in markdown)."""
    import re

    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        candidates.append(text[first_brace : last_brace + 1])

    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _to_subsidy_program(payload: dict[str, Any]) -> SubsidyProgram | None:
    forms_raw = payload.get("forms") or []
    forms: list[SubsidyForm] = []
    for f in forms_raw:
        if not isinstance(f, dict):
            continue
        url = f.get("url") or None
        ext = (f.get("ext") or "").lower().strip(".") or "docx"
        form_id = str(f.get("form_id") or f.get("id") or "").strip()
        name = str(f.get("name") or "").strip()
        if not form_id:
            continue
        local_path = f"templates/{payload.get('program_id', 'unknown')}/{form_id}.{ext}"
        try:
            forms.append(
                SubsidyForm(
                    form_id=form_id,
                    name=name or form_id,
                    url=url,
                    local_path=local_path,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("skip malformed form entry %s: %s", f, e)

    try:
        return SubsidyProgram(
            program_id=str(payload.get("program_id") or "unknown"),
            round_number=int(payload.get("round_number") or 0),
            canonical_name=str(payload.get("canonical_name") or ""),
            short_name=str(payload.get("short_name") or ""),
            issuing_body=str(payload.get("issuing_body") or ""),
            landing_url=payload.get("landing_url") or None,
            guideline_pdf_url=payload.get("guideline_pdf_url") or None,
            application_deadline=payload.get("application_deadline") or None,
            max_award_yen=int(payload.get("max_award_yen") or 0),
            subsidy_rate=float(payload.get("subsidy_rate") or 0.0),
            forms=forms,
            keywords_for_research=list(payload.get("keywords_for_research") or []),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("SubsidyDiscoverer: failed to build SubsidyProgram: %s", e)
        return None
