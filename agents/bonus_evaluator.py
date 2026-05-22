"""Evaluate per-subsidy bonus items (加点項目) against a company profile.

Why this exists
---------------
Bonus items vary by subsidy:

  * 持続化補助金 第19回 : 事業環境変化加点 / 賃金引上げ枠 / 創業者加点 / ...
  * ものづくり補助金 第18次: 賃上げ加点 / サイバーセキュリティ / 知的財産 / ...
  * 省力化投資補助金 第2回: 賃上げ加点 / 大幅賃上げ / 地域経済牽引 / ...

The earlier ``tools/bonus_points.py`` was hardcoded for 持続化補助金 only —
running it against ものづくり補助金 would produce the wrong bonus block.

BonusEvaluator reads ``profile.bonus_items`` (which differs per subsidy)
and for each item decides:

  1. Is the item applicable to this company?
  2. If yes, generate body text targeting ``item.target_chars``.

Two modes:

  * ``"mock"`` — deterministic heuristic (offline CI / public demo)
  * ``"live"`` — Claude tool_use call (production, needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from config.settings import settings
from schemas.bonus_item import BonusItemResult, BonusItemSpec
from schemas.subsidy_profile import SubsidyProfile

logger = logging.getLogger(__name__)

Mode = Literal["mock", "live"]


# ---------------------------------------------------------------------------
# Heuristic detectors (mock mode)
# ---------------------------------------------------------------------------


_ENV_CHANGE_KEYWORDS = ("物価高騰", "原材料高騰", "円安", "輸送コスト", "ウクライナ")
_WAGE_KEYWORDS = ("賃金引上げ", "賃上げ", "事業場内最低賃金", "1人あたり")
_DEFICIT_KEYWORDS = ("赤字", "営業損失", "売上減少")


def _detect_env_change(company: dict) -> tuple[bool, str]:
    """Detect 事業環境変化加点 applicability."""
    blob = json.dumps(company, ensure_ascii=False)
    if any(kw in blob for kw in _ENV_CHANGE_KEYWORDS):
        return True, "原価上昇・物価高騰の言及あり"
    bonus = company.get("bonus_points") or {}
    if bonus.get("env_change"):
        return True, "bonus_points.env_change=True"
    return False, "物価高騰の影響を受けている記述が見つからない"


def _detect_wage_increase(company: dict) -> tuple[bool, str]:
    """Detect 賃金引上げ加点 applicability."""
    bonus = company.get("bonus_points") or {}
    if bonus.get("wage_increase"):
        return True, "bonus_points.wage_increase=True"
    blob = json.dumps(company, ensure_ascii=False)
    if any(kw in blob for kw in _WAGE_KEYWORDS):
        return True, "賃金引上げの言及あり"
    return False, "賃金引上げ計画が見つからない"


def _detect_deficit(company: dict) -> tuple[bool, str]:
    """Detect 赤字事業者 applicability."""
    bonus = company.get("bonus_points") or {}
    if bonus.get("deficit"):
        return True, "bonus_points.deficit=True"
    pl = (company.get("financial") or {}).get("past_3y_pl") or []
    if pl and any(p.get("operating_profit", 0) < 0 for p in pl):
        return True, "過去3期に営業損失あり"
    return False, "営業損失の記録なし"


def _detect_generic(item: BonusItemSpec, company: dict) -> tuple[bool, str]:
    """Generic keyword-based applicability for items we don't recognise."""
    hint = item.applicability_hint or ""
    if not hint:
        return False, "applicability_hint が無い（適用判定不可）"
    # Pull noun-like tokens from the hint and search company blob
    blob = json.dumps(company, ensure_ascii=False)
    nouns = [t for t in hint.replace("OR", " ").split() if len(t) >= 2]
    hits = [n for n in nouns if n in blob]
    if hits:
        return True, f"hint keywords matched: {', '.join(hits)}"
    return False, "applicability_hint と事業者プロファイルにマッチなし"


_ITEM_DETECTORS = {
    "env_change": _detect_env_change,
    "wage_increase": _detect_wage_increase,
    "deficit": _detect_deficit,
}


# ---------------------------------------------------------------------------
# Body generation
# ---------------------------------------------------------------------------


_MOCK_BODY_TEMPLATES = {
    "env_change": (
        "当社の主原料は、ロシア・ウクライナ情勢を背景とした輸送コストの上昇、"
        "急速な円安進行、産地国における気候変動の影響が複合的に重なり、"
        "{period}までに前年同期比+{cost_up}%まで上昇しています。これは過去10年で"
        "最も急激な原価上昇局面であり、当社の収益構造に直接的な影響を与えています。\n\n"
        "当社は{location}という性質上、顧客の多くが価格に敏感な日常使いの利用者であり、"
        "1杯あたり550〜650円の価格帯は地域の生活感覚に強く紐づいています。価格を一律"
        "+10%引き上げた場合、過去のシミュレーションでは月間客数が15〜20%減少すること"
        "が見込まれ、結果的に売上総額が減少するリスクが高い構造です。\n\n"
        "加えて電気代+{utility_up}%、賃料+{rent_up}%と固定費の増加も重なっており、"
        "現状の営業利益率は{prev_margin}%→{cur_margin}%まで悪化する見通しです。"
        "本補助事業による販路拡大は、価格転嫁に依存しない収益源確保の唯一の現実解で"
        "あり、当社の事業継続にとって不可欠な施策です。"
    ),
    "wage_increase": (
        "本補助事業の実施により、当社は売上+{growth}%、営業利益率の改善を見込み、"
        "事業場内最低賃金を地域別最低賃金より+50円以上引き上げる計画です。"
        "現在の事業場内最低賃金は{cur_min_wage}円、地域別最低賃金は{regional}円であり、"
        "本事業実施後は{new_min_wage}円（+{wage_up}円）まで引き上げます。\n\n"
        "1人あたり売上高は同期間で{cur_per_capita}千円→{new_per_capita}千円へ"
        "{per_capita_up}%改善し、賃金引上げの原資を内部留保ではなく事業成長で確保する"
        "計画とします。賃金引上げによる従業員の定着率向上、採用力強化、サービス品質"
        "の安定的維持も期待され、地域における雇用の質の向上にも資する取組みです。\n\n"
        "なお、賃金引上げの実施は本事業完了後の翌期から段階的に実施し、事業場内"
        "最低賃金の対象者を明確化した上で、賃金台帳の改定・社員説明会の開催・"
        "労使協議を通じて確実に実行します。賃金引上げ要件を満たさなかった場合の"
        "返還リスクについても認識した上で本枠を申請しております。"
    ),
    "deficit": (
        "当社は前年度（{prev_year}年度）に営業損失{loss}千円を計上しており、"
        "売上減少と原価上昇の複合要因により赤字決算となりました。原材料費の高騰"
        "（+{cost_up}%）、エネルギーコストの増加（+{utility_up}%）、人件費の維持的"
        "コスト増が同時に発生したため、価格転嫁できない当社の収益構造では赤字回避"
        "が困難でした。\n\n"
        "本補助事業による販路開拓・収益源多角化は、赤字構造を脱却し持続可能な経営"
        "基盤を再構築するために不可欠な投資です。具体的には店舗売上一本依存から"
        "EC・ギフト需要への分散により、外部要因の変動に強い収益構造を構築します。\n\n"
        "本補助事業終了後の{target_year}年度には営業黒字化（営業利益率"
        "{target_margin}%）を目指し、その後3年間で営業利益率を年率1.0ポイントずつ"
        "向上させる中期計画と紐付けて実行します。"
    ),
}


def _generate_body_mock(item: BonusItemSpec, company: dict) -> str:
    """Deterministic body generation. Uses item-specific template or
    a generic fallback that includes the hint text."""
    tmpl = _MOCK_BODY_TEMPLATES.get(item.item_id)
    if tmpl:
        return tmpl.format(
            period="2025年4月",
            cost_up=18,
            location="住宅街立地",
            utility_up=12,
            rent_up=5,
            prev_margin="6.4",
            cur_margin="4.8",
            growth=25,
            cur_min_wage=1_100,
            regional=1_113,
            new_min_wage=1_163,
            wage_up=50,
            cur_per_capita=5_700,
            new_per_capita=7_125,
            per_capita_up=25,
            prev_year=2024,
            loss=1_500,
            target_year=2026,
            target_margin="6.2",
        )

    # Generic fallback
    lines = [
        f"【{item.display_name}】",
        item.applicability_hint or "（適用条件の詳細は公募要領を参照）",
        "",
        "本補助事業を通じて、上記要件に該当する取組みを継続的に実施する計画です。",
        item.body_prompt_hint
        or "具体的な数値根拠、構造的理由、本補助事業との関連性を本欄で詳述します。",
    ]
    body = "\n".join(lines)
    if len(body) < item.min_chars:
        # Pad to clear the floor
        body += (
            "\n\n本要件は本事業のスケジュール（採択→交付決定→施策実施→実績報告）"
            "全段階を通して達成可能な構造で計画しています。"
        )
    if len(body) > item.max_chars:
        body = body[: item.max_chars - 3] + "…"
    return body


async def _generate_body_live(item: BonusItemSpec, company: dict) -> str:
    """Generate body text via Claude with item-specific instructions."""
    from tools.claude_client import call_claude

    system_prompt = (
        "あなたは日本の中小企業向け補助金申請書の執筆コーチです。"
        f"加点項目「{item.display_name}」の本文を、"
        f"事業者の事実関係に基づき、{item.min_chars}〜{item.max_chars}字で生成してください。"
        "前置きや説明文は一切出力せず、加点項目欄に貼り付ける本文だけを返してください。"
    )
    user_message = (
        f"## 加点項目\n"
        f"- 名称: {item.display_name}\n"
        f"- カテゴリ: {item.category}\n"
        f"- 適用条件のヒント: {item.applicability_hint}\n"
        f"- 本文で押さえるべきポイント: {item.body_prompt_hint}\n\n"
        f"## 申請事業者プロファイル\n{json.dumps(company, ensure_ascii=False, indent=2)}\n"
    )
    return (
        await call_claude(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.3,
            max_tokens=2048,
            cache_system=True,
        )
    ).strip()


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


class BonusEvaluator:
    """Evaluate every bonus item declared on the SubsidyProfile."""

    agent_id = "#bonus"
    agent_name = "BonusEvaluator"

    async def evaluate(
        self,
        profile: SubsidyProfile,
        company: dict[str, Any],
        *,
        mode: Mode = "mock",
    ) -> list[BonusItemResult]:
        results: list[BonusItemResult] = []
        for item in profile.bonus_items:
            detector = _ITEM_DETECTORS.get(item.item_id, _detect_generic)
            try:
                applicable, reasoning = detector(item, company) if detector is _detect_generic else detector(company)
            except TypeError:
                # Specific detector ignores ``item``; call with company only.
                applicable, reasoning = detector(company)

            body = ""
            if applicable:
                if mode == "live" and settings.anthropic_api_key:
                    try:
                        body = await _generate_body_live(item, company)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "BonusEvaluator live body failed for %s: %s; using mock",
                            item.item_id,
                            e,
                        )
                        body = _generate_body_mock(item, company)
                else:
                    body = _generate_body_mock(item, company)

            results.append(
                BonusItemResult(
                    item_id=item.item_id,
                    display_name=item.display_name,
                    applicable=applicable,
                    body_text=body,
                    reasoning=reasoning,
                )
            )
        return results


__all__ = ["BonusEvaluator"]
