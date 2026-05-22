"""Profile-driven live story builder.

Takes a ``SubsidyProfile`` and the company profile, generates content for
**every section the profile declares** — not a fixed 6-section schema.
This is the only way to handle a profile that was synthesised from a real
公募要領 PDF (where section IDs are publisher-specific, e.g.
``keiei_1`` / ``hojo_2`` / ``tokuten_1`` for 持続化補助金 第19回).

Generation is per-section: each section gets its own Claude call so that
we can give it the full ``target_chars`` budget rather than rationing
4,096 tokens across six sections (which was the bug the first run hit —
each section came back at ~400-600字 instead of the per-section target).
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

from schemas.subsidy_profile import SectionSpec, SubsidyProfile

logger = logging.getLogger(__name__)


_SECTION_SYSTEM = """\
あなたは日本の中小企業向け補助金申請書の専門コンサルタントです。
ユーザーから渡された「申請事業者プロファイル」と「公募要領コンテキスト」
に基づき、指定された1つのセクションの本文だけを返してください。

ルール:
- 事業者プロファイルにある具体数値（売上・客数・リピート率・SNS フォロワー数等）
  を本文に必ず織り込む
- 公募要領の不変条件（経費区分・審査基準・文字数上限）を守る
- 課題と施策の対応関係を「課題①→施策①」のように明示する
- 「約」「およそ」などの曖昧な表現は使わない（具体数値で書く）
- 出力は本文だけ。前置き・後置き・説明文・見出しは付けない
"""


async def generate_section(
    section: SectionSpec,
    *,
    company: dict[str, Any],
    canonical_name: str,
    extra_context: str = "",
) -> str:
    """Generate one section's body via Claude, sized to the section spec."""
    from tools.claude_client import call_claude

    user_message = (
        f"## 補助金\n{canonical_name}\n\n"
        f"## 目標セクション\n"
        f"- ID: {section.section_id}\n"
        f"- 名称: {section.display_name}\n"
        f"- 目標文字数: {section.target_chars} 字（min {section.min_chars} / max {section.max_chars}）\n\n"
        f"## 申請事業者プロファイル\n"
        f"{yaml.dump(company, allow_unicode=True, default_flow_style=False)}\n\n"
    )
    if extra_context:
        user_message += f"## 公募要領コンテキスト\n{extra_context}\n\n"
    user_message += (
        f"上記を踏まえ、セクション「{section.display_name}」の本文を生成してください。"
        f"本文の文字数は {section.min_chars}〜{section.max_chars} 字に収め、"
        f"{section.target_chars} 字付近を目安としてください。"
    )

    # Per-section call gets its own token budget, so each section can reach
    # its target_chars instead of competing with siblings for 4,096 tokens.
    text = await call_claude(
        system_prompt=_SECTION_SYSTEM,
        user_message=user_message,
        temperature=0.3,
        max_tokens=4_096,
        cache_system=True,
    )
    return text.strip()


async def build_story_for_profile(
    company: dict[str, Any],
    profile: SubsidyProfile,
    *,
    research_findings: str = "",
) -> dict[str, str]:
    """Generate body text for every section in ``profile.sections``.

    Returns a dict keyed by ``section.section_id`` — exactly what the
    downstream document_assembler and length_validator expect.
    """
    story: dict[str, str] = {}
    for spec in profile.sections:
        try:
            text = await generate_section(
                spec,
                company=company,
                canonical_name=profile.canonical_name,
                extra_context=research_findings,
            )
            story[spec.section_id] = text
            logger.info(
                "story: %s → %d chars (target %d)",
                spec.section_id,
                len(text),
                spec.target_chars,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "story generation failed for %s: %s; leaving empty",
                spec.section_id,
                e,
            )
            story[spec.section_id] = ""
    return story


# ---------------------------------------------------------------------------
# Legacy entry point (kept for any caller that still uses fixed schema)
# ---------------------------------------------------------------------------


_LEGACY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_overview": {"type": "string"},
        "sales_situation": {"type": "string"},
        "challenges": {"type": "string"},
        "strategy": {"type": "string"},
        "expected_outcome": {"type": "string"},
        "bonus_env_change": {"type": "string"},
    },
    "required": [
        "company_overview", "sales_situation", "challenges",
        "strategy", "expected_outcome", "bonus_env_change",
    ],
}


async def build_story_live(company: dict, guideline_text: str) -> dict:
    """Legacy fixed-schema entry point (deprecated).

    Prefer ``build_story_for_profile`` for any new code — it generates
    content per the actual profile's section structure, not a fixed
    6-section template.
    """
    from tools.claude_client import call_claude_json

    user_message = (
        f"## 公募要領\n{guideline_text}\n\n"
        f"## 申請事業者プロファイル\n{yaml.dump(company, allow_unicode=True)}\n\n"
        "上記の事業者プロファイルと公募要領に基づいて、申請書の主要セクションを書いてください。"
    )
    return await call_claude_json(
        system_prompt=(
            "あなたは小規模事業者の補助金申請書を書く専門コンサルタントです。"
            "出力は必ず指定のJSON Schemaに従ってください。"
        ),
        user_message=user_message,
        json_schema=_LEGACY_SCHEMA,
        tool_name="build_application_story",
        max_tokens=8_192,
    )
