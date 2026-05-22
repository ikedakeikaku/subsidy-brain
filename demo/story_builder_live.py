"""Build an application story by calling Claude directly.

Extracted from the old ``run_demo.py`` so the natural-language pipeline
has no dependency on demo-time sample fixtures (``sample_guideline.md`` etc.).

The guideline text is now supplied by the caller — typically the natural
pipeline passes either the cached guideline PDF text (via the
GuidelineParser) or a brief summary that the agent assembled itself.
"""
from __future__ import annotations

from typing import Any

import yaml

_SYSTEM_PROMPT = (
    "あなたは小規模事業者の補助金申請書を書く専門コンサルタントです。"
    "公募要領の不変条件（経費区分・文字数上限・審査基準）を必ず守り、"
    "事業者固有のデータ（自社強み・課題・経費計画）に基づいたストーリーを構築してください。"
    "出力は必ず指定のJSON Schemaに従ってください。"
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_overview": {
            "type": "string",
            "description": "1-1. 自社の概要（600字以内）",
        },
        "sales_situation": {
            "type": "string",
            "description": "1-2. 売上・利益の状況（800字以内）",
        },
        "challenges": {
            "type": "string",
            "description": "1-3. 経営課題（600字以内）",
        },
        "strategy": {
            "type": "string",
            "description": "4-2. 今後のプラン（1000字以内）",
        },
        "expected_outcome": {
            "type": "string",
            "description": "補助事業の効果（500字以内）",
        },
        "bonus_env_change": {
            "type": "string",
            "description": "事業環境変化加点本文（500字以内）",
        },
    },
    "required": [
        "company_overview",
        "sales_situation",
        "challenges",
        "strategy",
        "expected_outcome",
        "bonus_env_change",
    ],
}


async def build_story_live(company: dict, guideline_text: str) -> dict:
    """Call Claude through the project's structured-output helper."""
    from tools.claude_client import call_claude_json

    user_message = (
        f"## 公募要領\n{guideline_text}\n\n"
        f"## 申請事業者プロファイル\n{yaml.dump(company, allow_unicode=True)}\n\n"
        "上記の事業者プロファイルと公募要領に基づいて、申請書の主要セクションを書いてください。"
    )

    return await call_claude_json(
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
        json_schema=_SCHEMA,
        tool_name="build_application_story",
    )
