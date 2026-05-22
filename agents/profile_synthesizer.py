"""Synthesize a SubsidyProfile from a natural-language subsidy name.

The original architecture required a hand-written ``presets/<id>_profile.yaml``
for every subsidy. That's the same as a human consultant only being able
to work on subsidies they've memorised in advance — useless for "I want
to apply for X" requests that hit a fresh programme.

This agent does what a human consultant would do:

  1. Web-search the official 公募要領 and 様式 list.
  2. Read what it found and decide:
     - what sections the application must contain
     - what the character limit is for each
     - what charts / tables are expected
     - what 加点項目 exist
  3. Return a fully-formed ``SubsidyProfile`` that the downstream
     DocumentBuilder can consume directly — no YAML file needed.

If the web-search providers are unavailable, the synthesiser returns a
sensible default profile (8 sections, 加点 + grafs + 2 tables) so the
public CI demo still completes. Real production runs override this
fallback by providing ``ANTHROPIC_API_KEY``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from config.settings import settings
from schemas.subsidy_profile import (
    ChartSpec,
    ChartType,
    SectionSpec,
    SubsidyProfile,
    TableSpec,
    TableType,
)
from tools.web_search import web_search

logger = logging.getLogger(__name__)


_SYNTHESIS_SYSTEM = """\
あなたは日本の中小企業向け補助金の調査スペシャリストです。
ユーザーが指定した補助金について、公式 公募要領 と 様式 一式を調査し、
申請書の作成に必要な「セクション構造」を JSON で返してください。

ルール:
- 公式サイト（中小企業庁・経産省・全国商工会連合会・商工会議所 等）の最新情報を優先
- セクション ID は ASCII の `section_1_1` `business_summary` のような snake_case
- 各セクションに「目標文字数」「最小文字数」「最大文字数」を必ず付与（公募要領に明記が無ければ実申請書として妥当な数字を推定）
- 加点項目は別セクションとして列挙
- 必須グラフ・必須テーブルも判断して列挙

期待するJSONスキーマ:
{
  "program_id": "短いASCII識別子（例: jizoku_19）",
  "canonical_name": "公式名称（第N回まで含む）",
  "quality_score_target": 85,
  "sections": [
    {
      "section_id": "section_1_1",
      "display_name": "1-1. 自社の概要",
      "target_chars": 600,
      "min_chars": 450,
      "max_chars": 800,
      "requires_data_paths": ["company.name", "company.industry"]
    },
    ...
  ],
  "charts": [
    {
      "chart_id": "chart_revenue_trend",
      "chart_type": "revenue_trend",
      "title": "...",
      "data_path": "financial.past_3y_pl",
      "place_after_section": "section_1_2"
    }
  ],
  "tables": [
    {
      "table_id": "table_schedule",
      "table_type": "schedule",
      "title": "...",
      "columns": ["時期", "項目", "内容"],
      "data_path": "planned_project.schedule",
      "place_after_section": "section_4_2"
    }
  ]
}

JSON以外の前置きや解説は一切出力せず、JSONオブジェクトのみを返してください。
"""


# Sentinel default profile used when web-search is unavailable so the
# public CI / offline demo keeps working without external credentials.
_DEFAULT_FALLBACK_PROFILE: dict[str, Any] = {
    "program_id": "generic_subsidy",
    "canonical_name": "汎用補助金プロファイル（フォールバック）",
    "quality_score_target": 80,
    "sections": [
        {"section_id": "section_1_1", "display_name": "1-1. 自社の概要",
         "target_chars": 600, "min_chars": 450, "max_chars": 800,
         "requires_data_paths": ["company.name", "company.business_description"]},
        {"section_id": "section_1_2", "display_name": "1-2. 売上・利益の状況",
         "target_chars": 800, "min_chars": 600, "max_chars": 1000,
         "requires_data_paths": ["financial.past_3y_pl"]},
        {"section_id": "section_1_3", "display_name": "1-3. 経営課題",
         "target_chars": 600, "min_chars": 450, "max_chars": 800,
         "requires_data_paths": ["challenges"]},
        {"section_id": "section_2_1", "display_name": "2-1. 市場の動向",
         "target_chars": 800, "min_chars": 600, "max_chars": 1000,
         "requires_data_paths": ["target_market"]},
        {"section_id": "section_3", "display_name": "3. 強み・弱み",
         "target_chars": 600, "min_chars": 450, "max_chars": 800,
         "requires_data_paths": ["strengths"]},
        {"section_id": "section_4_2", "display_name": "4-2. 今後のプラン",
         "target_chars": 1200, "min_chars": 900, "max_chars": 1500,
         "requires_data_paths": ["planned_project.initiatives"]},
        {"section_id": "section_effect", "display_name": "補助事業の効果",
         "target_chars": 600, "min_chars": 450, "max_chars": 800,
         "requires_data_paths": ["planned_project.expected_outcomes"]},
        {"section_id": "bonus_env_change", "display_name": "【加点】事業環境変化",
         "target_chars": 500, "min_chars": 350, "max_chars": 600},
    ],
    "charts": [
        {"chart_id": "chart_revenue_trend", "chart_type": "revenue_trend",
         "title": "過去3期の売上推移",
         "data_path": "financial.past_3y_pl",
         "place_after_section": "section_1_2"},
        {"chart_id": "chart_effect_before_after", "chart_type": "effect_before_after",
         "title": "補助事業による売上効果",
         "data_path": "planned_project.expected_outcomes",
         "place_after_section": "section_effect"},
    ],
    "tables": [
        {"table_id": "table_schedule", "table_type": "schedule",
         "title": "スケジュール",
         "columns": ["時期", "項目", "内容"],
         "data_path": "planned_project.schedule",
         "place_after_section": "section_4_2"},
        {"table_id": "table_expense", "table_type": "expense_breakdown",
         "title": "経費明細",
         "columns": ["区分", "明細", "金額（円）"],
         "data_path": "expenses.breakdown",
         "place_after_section": "section_effect"},
    ],
}


class ProfileSynthesizer:
    """Turn a natural-language subsidy name into a SubsidyProfile."""

    agent_id = "#profile"
    agent_name = "ProfileSynthesizer"

    async def synthesize(
        self, subsidy_name: str, *, extra_context: str = ""
    ) -> SubsidyProfile:
        """Return a SubsidyProfile for ``subsidy_name``.

        Web-search providers are tried in order. If all fail, the fallback
        default profile is returned and a warning is logged.
        """
        if not (settings.anthropic_api_key or settings.perplexity_api_key):
            logger.info(
                "ProfileSynthesizer: no web-search credential set; using fallback."
            )
            return self._fallback_profile(subsidy_name)

        query = self._build_query(subsidy_name, extra_context)
        result = await web_search(query, system_prompt=_SYNTHESIS_SYSTEM)
        if not result.answer:
            logger.warning(
                "ProfileSynthesizer: empty answer from %s; using fallback.",
                result.provider,
            )
            return self._fallback_profile(subsidy_name)

        payload = self._extract_json(result.answer)
        if not payload:
            logger.warning("ProfileSynthesizer: no JSON in response; using fallback.")
            return self._fallback_profile(subsidy_name)

        return self._to_profile(payload, fallback_name=subsidy_name)

    # ------------------------------------------------------------------

    def _build_query(self, subsidy_name: str, extra_context: str) -> str:
        body = (
            f"対象補助金: {subsidy_name}\n\n"
            "上記の補助金の最新の公募要領を調べ、申請書のセクション構造、"
            "各セクションの推奨文字数、必須グラフ・必須テーブル、加点項目を"
            "JSON で返してください。"
        )
        if extra_context:
            body += f"\n\n## 追加コンテキスト\n{extra_context}"
        return body

    def _fallback_profile(self, subsidy_name: str) -> SubsidyProfile:
        payload = dict(_DEFAULT_FALLBACK_PROFILE)
        payload["canonical_name"] = f"{subsidy_name}（フォールバック構造）"
        return self._to_profile(payload, fallback_name=subsidy_name)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
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

    @staticmethod
    def _to_profile(
        payload: dict[str, Any], *, fallback_name: str
    ) -> SubsidyProfile:
        sections: list[SectionSpec] = []
        for raw in payload.get("sections") or []:
            try:
                sections.append(SectionSpec(**_sanitize_section(raw)))
            except Exception as e:  # noqa: BLE001
                logger.warning("skip malformed section %s: %s", raw, e)

        charts: list[ChartSpec] = []
        for raw in payload.get("charts") or []:
            try:
                charts.append(_to_chart_spec(raw))
            except Exception as e:  # noqa: BLE001
                logger.warning("skip malformed chart %s: %s", raw, e)

        tables: list[TableSpec] = []
        for raw in payload.get("tables") or []:
            try:
                tables.append(_to_table_spec(raw))
            except Exception as e:  # noqa: BLE001
                logger.warning("skip malformed table %s: %s", raw, e)

        program_id = (
            str(payload.get("program_id") or "")
            or _slugify(fallback_name)
        )

        return SubsidyProfile(
            program_id=program_id,
            canonical_name=str(payload.get("canonical_name") or fallback_name),
            sections=sections or _fallback_sections(),
            charts=charts,
            tables=tables,
            quality_score_target=int(payload.get("quality_score_target") or 80),
        )


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _sanitize_section(raw: dict) -> dict:
    return {
        "section_id": str(raw.get("section_id") or "").strip(),
        "display_name": str(raw.get("display_name") or "").strip(),
        "target_chars": int(raw.get("target_chars") or 600),
        "min_chars": int(raw.get("min_chars") or 450),
        "max_chars": int(raw.get("max_chars") or 800),
        "requires_data_paths": list(raw.get("requires_data_paths") or []),
    }


def _to_chart_spec(raw: dict) -> ChartSpec:
    ctype = str(raw.get("chart_type") or "revenue_trend").lower()
    return ChartSpec(
        chart_id=str(raw.get("chart_id") or "chart"),
        chart_type=ChartType(ctype) if ctype in {c.value for c in ChartType} else ChartType.REVENUE_TREND,
        title=str(raw.get("title") or ""),
        data_path=str(raw.get("data_path") or "financial.past_3y_pl"),
        place_after_section=str(raw.get("place_after_section") or ""),
    )


def _to_table_spec(raw: dict) -> TableSpec:
    ttype = str(raw.get("table_type") or "schedule").lower()
    return TableSpec(
        table_id=str(raw.get("table_id") or "table"),
        table_type=TableType(ttype) if ttype in {t.value for t in TableType} else TableType.SCHEDULE,
        title=str(raw.get("title") or ""),
        columns=list(raw.get("columns") or []),
        data_path=str(raw.get("data_path") or ""),
        place_after_section=str(raw.get("place_after_section") or ""),
    )


def _slugify(name: str) -> str:
    return re.sub(r"\W+", "_", name.lower()).strip("_") or "subsidy"


def _fallback_sections() -> list[SectionSpec]:
    return [SectionSpec(**_sanitize_section(s)) for s in _DEFAULT_FALLBACK_PROFILE["sections"]]
