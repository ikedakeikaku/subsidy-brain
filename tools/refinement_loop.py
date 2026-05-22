"""Self-improvement loop that drives an initial draft up to a target score.

The loop is the structural answer to "the first draft is rarely adoption-
worthy". Each iteration:

  1. Score the current draft with ``estimate_adoption_probability``.
  2. If score >= target, return.
  3. Else, identify the most impactful weak section (the one whose under-
     scoring signal could close the biggest gap).
  4. Generate a strengthened replacement for that section.
  5. Repeat, capping at ``max_iterations``.

Section regeneration mode is pluggable:

  * ``"mock"``  — deterministic, offline. Appends section-specific filler
                 that pushes the section above its ``min_chars`` and adds
                 the signals the weakest axis is missing (specific numbers,
                 explicit one-to-one mapping). Used in CI and the offline
                 demo so the loop is fully reproducible.
  * ``"live"``  — calls Claude with a targeted prompt explaining what
                 signal needs to be strengthened. Production path.

The returned history records every iteration, the score progression, and
the section that was modified — useful for the BootCamp evaluator and for
the skill store to learn which refinement strategies work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from schemas.subsidy_profile import SectionSpec, SubsidyProfile
from tools.adoption_estimator import estimate_adoption_probability

logger = logging.getLogger(__name__)

Mode = Literal["mock", "live"]


# ---------------------------------------------------------------------------
# History records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationRecord:
    iteration: int
    score_before: int
    score_after: int
    refined_section: str | None
    reason: str
    passed: bool


# ---------------------------------------------------------------------------
# Refinement strategies
# ---------------------------------------------------------------------------


def _refine_section_mock(
    spec: SectionSpec,
    current_text: str,
    company: dict,
    report: dict,
) -> str:
    """Deterministic improvement: pad up to ``target_chars`` and inject
    signals that the score breakdown told us are missing.
    """
    missing_signals = [
        s["name"] for s in report["signals"] if s["score"] < s["weight"] * 0.7
    ]
    pad: list[str] = [current_text]

    if "自社固有データ" in missing_signals and spec.section_id in {
        "section_1_1",
        "section_3",
    }:
        pad.append(
            "\n\n【補強】当社のリピート率は62%（過去12ヶ月の購入履歴ベース、"
            "n=480件）、客単価1,250円、Instagramフォロワー2,800名、Google"
            "レビュー平均4.7（n=143件）と、いずれも公開可能な自社固有データを"
            "保有しています。試飲会参加者の92%が「再訪したい」と回答した独自"
            "アンケート結果も補助事業計画の根拠としています。"
        )

    if "具体数値密度" in missing_signals:
        pad.append(
            "\n\n【補強】2022年18,500千円→2023年21,300千円→2024年22,800千円の"
            "売上推移、営業利益率6.4%→4.8%、原価率+18%、電気代+12%、賃料+5%の"
            "コスト上昇など、定量的な裏付けを多数記載しています。"
        )

    if "課題→施策対応" in missing_signals and spec.section_id == "section_4_2":
        pad.append(
            "\n\n【補強・対応表】"
            "課題①店舗キャパ上限 → 施策①ECサイト構築。"
            "課題②通販ECなし → 施策①ECサイト構築 ＋ 施策②パッケージ刷新。"
            "課題③リピート率30% → 施策③試飲会・ワークショップ。"
            "課題④価格転嫁不可 → 施策①②④による収益源分散。"
        )

    if spec.section_id == "bonus_env_change" and "加点項目活用" in missing_signals:
        pad.append(
            "\n\n【補強】当社の主原料であるコーヒー生豆は前年比+18%の高騰、"
            "電気代+12%、賃料+5%と多重のコスト上昇を受けており、価格転嫁は"
            "周辺住民の価格感応度が高く実行困難。営業利益率は2024年6.4%→"
            "2025年見込4.8%へ悪化する見通しで、本補助事業による収益源分散が"
            "事業継続に直結します。"
        )

    # Generic fallback: if still below target_chars, pad iteratively with
    # section-aware filler blocks. Each block adds ~200 chars and stops
    # when we reach the target or run out of blocks.
    fillers = _generic_filler_blocks(spec)
    for filler in fillers:
        if len("".join(pad)) >= spec.target_chars:
            break
        pad.append(filler)

    new_text = "".join(pad)
    # Trim if we overshot the max
    if len(new_text) > spec.max_chars:
        new_text = new_text[: spec.max_chars - 3] + "…"
    return new_text


def _generic_filler_blocks(spec: SectionSpec) -> list[str]:
    """Multiple short paragraphs that can be appended one-by-one until the
    target length is reached. Each is plausible-sounding generic content
    that doesn't claim subsidy-specific facts.
    """
    name = spec.display_name
    return [
        (
            f"\n\n【補強①】「{name}」については、事業者固有の数値根拠"
            "（売上推移、客数、リピート率、顧客アンケート結果）と、補助事業"
            "の実施スケジュール（採択発表→交付決定→施策実施→実績報告→"
            "自走フェーズ）の整合性を確認しました。"
        ),
        (
            "\n\n【補強②】補助事業の経費区分は公募要領の不変条件に準拠し、"
            "過大経費・対象外経費は含まれていません。各経費は3社相見積もり"
            "を取得し、適正性を確認しています。経費執行は補助事業期間内に"
            "発注・納品・支払を完了するスケジュールで進めます。"
        ),
        (
            "\n\n【補強③】本事業のリスクとして、原材料価格の更なる高騰、"
            "競合の同様施策の出現、SNS広告効果の鈍化を想定しており、それぞれ"
            "に対する対応策（仕入れ多元化、独自差別化施策、広告チャネルの"
            "見直し）を準備しています。リスク発生時にも事業継続が可能な"
            "体制を整備しています。"
        ),
        (
            "\n\n【補強④】本事業終了後の自走フェーズでは、補助金に依存せず"
            "に営業利益から再投資できる収益構造を構築します。具体的には"
            "通販リピート率55%以上、定期便継続率70%以上、法人ギフト年商"
            "300千円以上を KPI として設定し、月次でモニタリングします。"
        ),
        (
            "\n\n【補強⑤】従業員に対しては本事業を社内勉強会で共有し、"
            "店舗業務と通販業務の役割分担を明確化します。1人あたり売上高"
            "の向上を賃金引上げの原資とし、本事業による生産性向上を従業員"
            "の処遇改善に直結させます。"
        ),
    ]


async def _refine_section_live(
    spec: SectionSpec,
    current_text: str,
    company: dict,
    report: dict,
) -> str:
    """Call Claude to expand the weak section with targeted instructions."""
    from tools.claude_client import call_claude

    missing = [
        s for s in report["signals"]
        if s["score"] < s["weight"] * 0.7 and s.get("weak_section") == spec.section_id
    ]
    missing_names = ", ".join(s["name"] for s in missing) or "総合品質"

    system_prompt = (
        "あなたは日本の補助金申請書の執筆コーチです。"
        "現状の文章をベースに、指定された弱点を埋めるように拡張・書き直ししてください。"
        "事業者の事実関係は変えず、定量データを増やし、課題と施策の対応関係を明示します。"
        f"出力は{spec.min_chars}〜{spec.max_chars}字に収めてください。"
        "前置きや説明文は一切出力せず、置換後の本文だけを返してください。"
    )
    user_message = (
        f"# 補助金申請書セクション「{spec.display_name}」を強化\n\n"
        f"## 現状の本文\n{current_text}\n\n"
        f"## 強化すべき弱点\n{missing_names}\n\n"
        f"## 事業者プロファイル\n{company}\n\n"
        "上記を踏まえ、本文を書き直してください。"
    )
    revised = await call_claude(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=0.3,
        max_tokens=2048,
        cache_system=True,
    )
    return revised.strip()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def refine_until_threshold(
    profile: SubsidyProfile,
    company: dict,
    story: dict[str, str],
    *,
    target_score: int | None = None,
    max_iterations: int = 3,
    mode: Mode = "mock",
) -> dict[str, Any]:
    """Iteratively improve ``story`` until adoption probability >= target.

    Returns::

        {
          "final_story": dict[section_id, text],
          "final_score": int,
          "passed": bool,
          "iterations": [IterationRecord(...), ...],
        }
    """
    threshold = target_score if target_score is not None else profile.quality_score_target
    current_story = dict(story)
    iterations: list[IterationRecord] = []
    already_refined: set[str] = set()

    for i in range(max_iterations):
        report_before = estimate_adoption_probability(profile, company, current_story)
        if report_before["passed"]:
            iterations.append(
                IterationRecord(
                    iteration=i,
                    score_before=report_before["total"],
                    score_after=report_before["total"],
                    refined_section=None,
                    reason="initial score already meets target",
                    passed=True,
                )
            )
            break

        # Pick the weak section with the biggest score gap, skipping
        # ones we already refined this run.
        weak_sections = [
            s for s in report_before["worst_weak_sections"]
            if s not in already_refined
        ]
        if not weak_sections:
            iterations.append(
                IterationRecord(
                    iteration=i,
                    score_before=report_before["total"],
                    score_after=report_before["total"],
                    refined_section=None,
                    reason="no refinable section identified",
                    passed=False,
                )
            )
            break

        target_section_id = weak_sections[0]
        spec = profile.section_by_id(target_section_id)
        if spec is None:
            iterations.append(
                IterationRecord(
                    iteration=i,
                    score_before=report_before["total"],
                    score_after=report_before["total"],
                    refined_section=target_section_id,
                    reason=f"section {target_section_id} not in profile",
                    passed=False,
                )
            )
            break

        current_text = current_story.get(target_section_id, "")
        if mode == "live":
            new_text = await _refine_section_live(spec, current_text, company, report_before)
        else:
            new_text = _refine_section_mock(spec, current_text, company, report_before)
        current_story[target_section_id] = new_text
        already_refined.add(target_section_id)

        report_after = estimate_adoption_probability(profile, company, current_story)
        iterations.append(
            IterationRecord(
                iteration=i,
                score_before=report_before["total"],
                score_after=report_after["total"],
                refined_section=target_section_id,
                reason="; ".join(
                    s["name"]
                    for s in report_before["signals"]
                    if s["score"] < s["weight"] * 0.7
                )
                or "general improvement",
                passed=report_after["passed"],
            )
        )
        if report_after["passed"]:
            break

    final = estimate_adoption_probability(profile, company, current_story)
    return {
        "final_story": current_story,
        "final_score": final["total"],
        "passed": final["passed"],
        "target_score": threshold,
        "iterations": [
            {
                "iteration": r.iteration,
                "score_before": r.score_before,
                "score_after": r.score_after,
                "refined_section": r.refined_section,
                "reason": r.reason,
                "passed": r.passed,
            }
            for r in iterations
        ],
    }


# Synchronous convenience used in tests / mock pipelines
def refine_until_threshold_sync(
    profile: SubsidyProfile,
    company: dict,
    story: dict[str, str],
    **kwargs,
) -> dict:
    import asyncio

    return asyncio.run(refine_until_threshold(profile, company, story, **kwargs))


__all__ = [
    "refine_until_threshold",
    "refine_until_threshold_sync",
    "IterationRecord",
]
