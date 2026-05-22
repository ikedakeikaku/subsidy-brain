"""Research adoption examples for a target subsidy program.

For a given ``SubsidyProgram`` and an industry hint, query the web for
real-world adoption cases, extract structured findings, and persist them to
the skill store under ``knowledge/adoption_patterns/<program_id>__<industry>``.

The default provider is **Anthropic web search** (Claude's built-in
``web_search`` tool), with Perplexity as fallback. Without either key the
researcher is a no-op (logs a warning, returns an empty findings list) so
CI and the public demo stay runnable.
"""
from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from schemas.subsidy_registry import SubsidyProgram
from tools.skill_store import skill_store
from tools.web_search import web_search

logger = logging.getLogger(__name__)


_RESEARCH_SYSTEM = """\
あなたは日本の中小企業向け補助金の調査アナリストです。
公開されている採択案件・支援機関の事例集・実績報告から、
申請書を書く実務家が再現できるレベルの「具体的な記述パターン」を抽出してください。

出力ルール:
- 採択事業名・事業者名は要約してOK（個人情報の引用は避ける）
- 事業内容・施策・記載のコツを箇条書きで3〜6点
- 各点に出典URLを添える
- 採択を分けた決定要因と思われる点を最後に1〜2行で総括する
"""


def _user_message(program: SubsidyProgram, industry: str) -> str:
    name = program.canonical_name
    keywords = program.keywords_for_research or [f"{name} 採択事例 {industry}"]
    return (
        f"対象補助金: {name}（第{program.round_number}回, 発行: {program.issuing_body}）\n"
        f"想定業種: {industry}\n"
        f"検索キーワード候補: {', '.join(keywords)}\n\n"
        "上記の補助金について、業種に合った採択事例・記述ポイントを調べてください。"
    )


class AdoptionResearcher:
    """Run a single research round and persist the findings."""

    agent_id = "#research"
    agent_name = "AdoptionResearcher"

    async def research(
        self, program: SubsidyProgram, industry: str
    ) -> dict[str, Any]:
        """Fetch findings; return a manifest. Always safe to call."""
        manifest: dict[str, Any] = {
            "program_id": program.program_id,
            "industry": industry,
            "available": False,
            "provider": "none",
            "findings": "",
            "citations": [],
            "knowledge_key": "",
        }

        if not (settings.anthropic_api_key or settings.perplexity_api_key):
            logger.info(
                "AdoptionResearcher: no web-search credential set; skipping research."
            )
            return manifest

        result = await web_search(
            _user_message(program, industry),
            system_prompt=_RESEARCH_SYSTEM,
        )
        if not result.answer:
            logger.info("AdoptionResearcher: empty answer from %s", result.provider)
            return manifest

        key = f"adoption_patterns__{program.program_id}__{industry}"
        skill_store.set_knowledge(
            key,
            {
                "program_id": program.program_id,
                "industry": industry,
                "provider": result.provider,
                "findings": result.answer,
                "citations": result.citations,
            },
        )
        manifest.update(
            {
                "available": True,
                "provider": result.provider,
                "findings": result.answer,
                "citations": result.citations,
                "knowledge_key": key,
            }
        )
        return manifest
