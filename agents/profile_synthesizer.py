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
from schemas.bonus_item import BonusItemSpec
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


# ---------------------------------------------------------------------------
# Fallback profiles
# ---------------------------------------------------------------------------
# Used when web search is unavailable (no API key). One fallback per major
# subsidy family — section structures and bonus_items differ enough that a
# single generic shape produces wrong output (e.g. 加点項目 names vary).
#
# Target chars are set to **the upper realistic range** of real adoption-
# quality applications, so the offline demo's output looks like a real
# draft rather than a thin stub. Refinement loop will pad as needed when
# the live LLM is unavailable.

# Common chart / table specs shared by every fallback
_COMMON_CHARTS: list[dict] = [
    {"chart_id": "chart_revenue_trend", "chart_type": "revenue_trend",
     "title": "過去3期の売上推移",
     "data_path": "financial.past_3y_pl",
     "place_after_section": "section_1_2"},
    {"chart_id": "chart_effect_before_after", "chart_type": "effect_before_after",
     "title": "補助事業による売上効果",
     "data_path": "planned_project.expected_outcomes",
     "place_after_section": "section_effect"},
]
_COMMON_TABLES: list[dict] = [
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
]


def _section(
    sid: str, name: str, target: int, *, paths: list[str] | None = None
) -> dict:
    """Build a SectionSpec dict with min/max defaulted to 65%/130% of target.

    Min is set generously (65% of target) because:
      * Real adoption-quality applications often hit 70–85% of formal max,
        not target.
      * Refinement loop is allowed to pad up to target; we don't want it
        to falsely flag every section as "underfilled" on first draft.
    """
    return {
        "section_id": sid,
        "display_name": name,
        "target_chars": target,
        "min_chars": int(target * 0.65),
        "max_chars": int(target * 1.3),
        "requires_data_paths": paths or [],
    }


# --- 持続化補助金 第19回 通常枠 -------------------------------------------
_JIZOKU_FALLBACK: dict[str, Any] = {
    "program_id": "jizoku_19",
    "canonical_name": "小規模事業者持続化補助金 第19回 通常枠",
    "quality_score_target": 85,
    "sections": [
        _section("section_1_1", "1-1. 自社の概要", 900,
                 paths=["company.name", "company.business_description"]),
        _section("section_1_2", "1-2. 売上・利益の状況", 1100,
                 paths=["financial.past_3y_pl"]),
        _section("section_1_3", "1-3. 経営課題", 900, paths=["challenges"]),
        _section("section_2_1", "2-1. 市場の動向", 1100, paths=["target_market"]),
        _section("section_3", "3. 強み・弱み", 900, paths=["strengths"]),
        _section("section_4_2", "4-2. 今後のプラン", 1800,
                 paths=["planned_project.initiatives"]),
        _section("section_effect", "補助事業の効果", 900,
                 paths=["planned_project.expected_outcomes"]),
    ],
    "charts": _COMMON_CHARTS,
    "tables": _COMMON_TABLES,
    "bonus_items": [
        {"item_id": "env_change", "display_name": "事業環境変化加点",
         "category": "重点政策加点", "weight_points": 5,
         "target_chars": 500, "min_chars": 350, "max_chars": 600,
         "applicability_hint": "物価高騰・原材料高騰・円安の影響を受け価格転嫁困難",
         "body_prompt_hint": "具体的な原価上昇率、価格転嫁不可の構造的理由（顧客層・立地等）、固定費上昇、営業利益率の悪化見通しを記載"},
        {"item_id": "wage_increase", "display_name": "賃金引上げ加点",
         "category": "政策加点", "weight_points": 5,
         "target_chars": 500, "min_chars": 350, "max_chars": 600,
         "applicability_hint": "事業場内最低賃金を地域別最低賃金+50円以上に引き上げ",
         "body_prompt_hint": "現状と引上げ後の事業場内最低賃金、地域別最低賃金との関係、引上げ原資の確保方法、対象人数を記載"},
        {"item_id": "deficit", "display_name": "赤字事業者加点",
         "category": "政策加点", "weight_points": 5,
         "target_chars": 400, "min_chars": 300, "max_chars": 500,
         "applicability_hint": "前年度決算が赤字 OR 直近1年で売上減少",
         "body_prompt_hint": "赤字に至った原因（複合要因含む）、本事業による黒字化計画、本事業終了後の収益見通しを記載"},
    ],
}


# --- ものづくり・商業・サービス補助金 第18次 -------------------------------
_MONOZUKURI_FALLBACK: dict[str, Any] = {
    "program_id": "monozukuri_v18",
    "canonical_name": "ものづくり・商業・サービス生産性向上促進補助金 第18次",
    "quality_score_target": 88,
    "sections": [
        _section("business_summary", "1. 事業概要", 1000,
                 paths=["company.name", "company.business_description"]),
        _section("project_purpose", "2-1. 補助事業の目的・概要", 1400,
                 paths=["planned_project.goal"]),
        _section("technical_advantage", "2-2. 技術的優位性・差別化", 1400,
                 paths=["strengths"]),
        _section("market_analysis", "2-3. 市場性・事業性", 1300,
                 paths=["target_market"]),
        _section("implementation_plan", "3-1. 実施体制とスケジュール", 1100,
                 paths=["planned_project.schedule"]),
        _section("investment_detail", "3-2. 設備投資の詳細と経費根拠", 1400,
                 paths=["expenses.breakdown"]),
        _section("financial_projection", "4. 収益計画（5ヶ年）", 1200,
                 paths=["planned_project.expected_outcomes"]),
        _section("productivity_kpi", "5. 生産性向上のKPI", 900),
        _section("risk_response", "6. リスクと対応策", 800),
    ],
    "charts": _COMMON_CHARTS,
    "tables": _COMMON_TABLES,
    "bonus_items": [
        {"item_id": "wage_increase", "display_name": "賃上げ加点",
         "category": "加点項目", "weight_points": 6,
         "target_chars": 500, "min_chars": 400, "max_chars": 700,
         "applicability_hint": "給与支給総額の年率1.5%以上引上げ＆事業場内最低賃金引上げ",
         "body_prompt_hint": "現状と引上げ後の給与支給総額、引上げ率、事業場内最低賃金、引上げ原資の確保方法を記載"},
        {"item_id": "cybersecurity", "display_name": "サイバーセキュリティ加点",
         "category": "加点項目", "weight_points": 3,
         "target_chars": 400, "min_chars": 300, "max_chars": 500,
         "applicability_hint": "情報セキュリティ対策（ISMS/Pマーク/サイバーセキュリティ対策自己宣言）",
         "body_prompt_hint": "実施している情報セキュリティ対策、認証取得状況、サイバーセキュリティ対策の自己宣言を記載"},
        {"item_id": "intellectual_property", "display_name": "知的財産加点",
         "category": "加点項目", "weight_points": 3,
         "target_chars": 400, "min_chars": 300, "max_chars": 500,
         "applicability_hint": "本事業に関わる特許・実用新案・意匠・商標の出願・取得",
         "body_prompt_hint": "本事業に関連する知的財産権の出願・取得状況、活用計画を記載"},
        {"item_id": "growth", "display_name": "成長性加点",
         "category": "加点項目", "weight_points": 4,
         "target_chars": 500, "min_chars": 400, "max_chars": 600,
         "applicability_hint": "経営革新計画 or 事業継続力強化計画の認定",
         "body_prompt_hint": "認定された計画の概要、本事業との連動、成長指標（KPI）の達成見込みを記載"},
        {"item_id": "financial_strength", "display_name": "財務基盤加点",
         "category": "加点項目", "weight_points": 3,
         "target_chars": 400, "min_chars": 300, "max_chars": 500,
         "applicability_hint": "自己資本比率・流動比率等の財務指標が一定水準以上",
         "body_prompt_hint": "直近の自己資本比率・流動比率・自己資本当期純利益率を記載"},
    ],
}


# --- 中小企業省力化投資補助金 第2回 ---------------------------------------
_SHORYOKUKA_FALLBACK: dict[str, Any] = {
    "program_id": "shoryokuka_v2",
    "canonical_name": "中小企業省力化投資補助金（一般型）第2回",
    "quality_score_target": 88,
    "sections": [
        _section("business_overview", "1. 事業者概要・現状", 1000,
                 paths=["company.name", "company.business_description"]),
        _section("labor_shortage_diagnosis", "2-1. 人手不足の現状と課題", 1100,
                 paths=["challenges"]),
        _section("investment_plan", "2-2. 省力化投資の内容と目的", 1400,
                 paths=["planned_project.initiatives"]),
        _section("productivity_uplift", "2-3. 生産性向上効果（労働生産性向上率）", 1100,
                 paths=["planned_project.expected_outcomes"]),
        _section("investment_payback", "2-4. 投資回収シミュレーション", 1100),
        _section("implementation_schedule", "3. 実施スケジュール", 800,
                 paths=["planned_project.schedule"]),
        _section("risk_management", "4. リスクと対応策", 700),
    ],
    "charts": _COMMON_CHARTS,
    "tables": _COMMON_TABLES,
    "bonus_items": [
        {"item_id": "wage_increase", "display_name": "賃金引上げ加点",
         "category": "加点項目", "weight_points": 5,
         "target_chars": 500, "min_chars": 400, "max_chars": 600,
         "applicability_hint": "給与支給総額の年率1.5%以上引上げ",
         "body_prompt_hint": "賃金引上げ額・対象人数・原資の確保方法を記載"},
        {"item_id": "substantial_wage_increase", "display_name": "大幅賃上げ加点",
         "category": "加点項目", "weight_points": 8,
         "target_chars": 500, "min_chars": 400, "max_chars": 600,
         "applicability_hint": "給与支給総額の年率6%以上引上げ＆事業場内最低賃金大幅引上げ",
         "body_prompt_hint": "大幅賃上げの規模、原資の確保根拠、人材投資の方針を記載"},
        {"item_id": "regional_leader", "display_name": "地域経済牽引加点",
         "category": "加点項目", "weight_points": 4,
         "target_chars": 400, "min_chars": 300, "max_chars": 500,
         "applicability_hint": "地域経済牽引事業計画の承認",
         "body_prompt_hint": "承認された地域経済牽引事業計画と本事業の関係を記載"},
    ],
}


# --- generic fallback (used when subsidy name doesn't match a known family) -
_GENERIC_FALLBACK: dict[str, Any] = {
    "program_id": "generic_subsidy",
    "canonical_name": "汎用補助金プロファイル（フォールバック）",
    "quality_score_target": 80,
    "sections": [
        _section("section_1_1", "1-1. 自社の概要", 900,
                 paths=["company.name", "company.business_description"]),
        _section("section_1_2", "1-2. 売上・利益の状況", 1100,
                 paths=["financial.past_3y_pl"]),
        _section("section_1_3", "1-3. 経営課題", 900, paths=["challenges"]),
        _section("section_2_1", "2-1. 市場の動向", 1100, paths=["target_market"]),
        _section("section_3", "3. 強み・弱み", 900, paths=["strengths"]),
        _section("section_4_2", "4-2. 今後のプラン", 1800,
                 paths=["planned_project.initiatives"]),
        _section("section_effect", "補助事業の効果", 900,
                 paths=["planned_project.expected_outcomes"]),
    ],
    "charts": _COMMON_CHARTS,
    "tables": _COMMON_TABLES,
    "bonus_items": [],  # unknown subsidy → no bonus item assumptions
}


def _pick_fallback_family(subsidy_name: str) -> dict[str, Any]:
    """Match the subsidy name to a known family. Returns a deep-copy."""
    import copy

    name = subsidy_name
    if "持続化" in name or "jizoku" in name.lower():
        return copy.deepcopy(_JIZOKU_FALLBACK)
    if "ものづくり" in name or "monozukuri" in name.lower():
        return copy.deepcopy(_MONOZUKURI_FALLBACK)
    if "省力化" in name or "shoryokuka" in name.lower():
        return copy.deepcopy(_SHORYOKUKA_FALLBACK)
    return copy.deepcopy(_GENERIC_FALLBACK)


# Legacy alias kept for backward compatibility with anything that imported
# the old constant name.
_DEFAULT_FALLBACK_PROFILE = _GENERIC_FALLBACK


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
        # Pick the closest-matching subsidy family — the bonus_items and
        # section structure differ enough (持続化 vs ものづくり vs 省力化)
        # that a single generic fallback would produce wrong output.
        payload = _pick_fallback_family(subsidy_name)
        # Override the program_id with a query-specific slug so distinct
        # queries (e.g. "持続化補助金 第19回" vs "持続化補助金 第20回") don't
        # collide on cache keys.
        payload["program_id"] = _slugify(subsidy_name)
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

        bonus_items: list[BonusItemSpec] = []
        for raw in payload.get("bonus_items") or []:
            try:
                bonus_items.append(BonusItemSpec(**raw))
            except Exception as e:  # noqa: BLE001
                logger.warning("skip malformed bonus_item %s: %s", raw, e)

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
            bonus_items=bonus_items,
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
