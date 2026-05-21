"""Adoption-probability estimator.

A heuristic but research-grounded scorer that goes beyond the simple
4-axis ``quality_scoring`` by incorporating signals that meaningfully
correlate with subsidy adoption in the Japanese SMB market:

  * 自社固有データの有無    — 採択案件は100%自社独自データを含む
  * 課題と施策の一対一対応  — 採択案件は課題N個に施策N個が紐づく
  * 加点項目の活用度        — 重点政策加点・政策加点の本文充実度
  * 具体数値の密度          — %・円・年度参照のヒット数
  * 文字数達成度            — profile の min/target/max への適合
  * 図表配置                — profile が要求するグラフ・表の充足

Output is a 0-100 score interpreted as an estimated adoption probability
(loosely calibrated against an internal corpus of public adoption results).
A breakdown shows where points were gained / lost so the refinement loop
knows which section to strengthen next.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from schemas.subsidy_profile import SubsidyProfile

# ---------------------------------------------------------------------------
# Signal extractors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    name: str
    weight: int        # max points this signal can contribute
    score: int         # actual points earned (<= weight)
    note: str
    weak_section: str | None = None   # which section to refine if low


def _signal_company_specific_data(company: dict, story: dict[str, str]) -> Signal:
    """Up to 25 points. Hard requirement for adoption."""
    full_text = "\n".join(story.values())
    company_block = company.get("company") or {}
    name = company_block.get("name", "")
    industry = company_block.get("industry", "")

    score = 0
    notes: list[str] = []

    if name and name in full_text:
        score += 6
        notes.append("自社名の文中言及あり")
    if industry and any(part in full_text for part in industry.split("（")[0].split()):
        score += 4
        notes.append("業種文脈の明示")

    # Specific in-house numbers (Instagram followers, repeat rate, ticket size)
    in_house_patterns = [
        (r"(?:フォロワー|登録者|会員)\s*[0-9,]+", "SNS/会員数"),
        (r"リピート率\s*[0-9.]+%", "リピート率"),
        (r"客単価\s*[0-9,]+", "客単価"),
        (r"レビュー(?:平均)?\s*[0-9.]+", "口コミスコア"),
        (r"アンケート\s*[0-9]+\s*(?:名|人|件)", "アンケート回答数"),
    ]
    in_house_hits = [name for pat, name in in_house_patterns if re.search(pat, full_text)]
    score += min(15, 3 * len(in_house_hits))
    if in_house_hits:
        notes.append("自社独自データ: " + ", ".join(in_house_hits))

    weak = None if score >= 15 else "section_1_1"
    return Signal(
        name="自社固有データ",
        weight=25,
        score=score,
        note="; ".join(notes) or "自社独自データが薄い（外部統計のみは不採択リスク）",
        weak_section=weak,
    )


def _signal_problem_solution_alignment(company: dict, story: dict[str, str]) -> Signal:
    """Up to 20 points. Each challenge should be matched to an initiative."""
    challenges = company.get("challenges") or []
    initiatives = (company.get("planned_project") or {}).get("initiatives") or []
    problem_text = story.get("section_1_3", "")
    plan_text = story.get("section_4_2", "")

    n_problems = max(len(challenges), problem_text.count("課題"))
    n_solutions = max(len(initiatives), plan_text.count("【施策"))

    notes: list[str] = []
    score = 0

    if n_problems >= 3 and n_solutions >= 3:
        score += 12
        notes.append(f"課題{n_problems}個 / 施策{n_solutions}個")
    elif n_problems > 0 and n_solutions > 0:
        score += 6
        notes.append(f"課題{n_problems}個 / 施策{n_solutions}個（やや少）")

    # Bonus: explicit one-to-one mapping language
    if "課題①" in problem_text and "施策①" in plan_text:
        score += 8
        notes.append("①②③の明示的対応関係あり")

    weak = "section_4_2" if score < 10 else None
    return Signal(
        name="課題→施策対応",
        weight=20,
        score=score,
        note="; ".join(notes) or "課題と施策の対応関係が不明確",
        weak_section=weak,
    )


def _signal_bonus_points_usage(company: dict, story: dict[str, str]) -> Signal:
    """Up to 15 points. 加点項目本文の充実度."""
    score = 0
    notes: list[str] = []
    env = story.get("bonus_env_change", "")
    bonus_block = company.get("bonus_points") or {}

    if bonus_block.get("env_change") and len(env) >= 300:
        score += 8
        notes.append(f"事業環境変化加点本文 {len(env)}字")
    if bonus_block.get("wage_increase"):
        score += 7
        notes.append("賃金引上げ加点を申請")

    weak = "bonus_env_change" if score < 8 else None
    return Signal(
        name="加点項目活用",
        weight=15,
        score=score,
        note="; ".join(notes) or "加点項目を活用していない",
        weak_section=weak,
    )


def _signal_specific_numbers(story: dict[str, str]) -> Signal:
    """Up to 15 points. Quantitative density."""
    full_text = "\n".join(story.values())
    pct = len(re.findall(r"\d+(?:\.\d+)?%", full_text))
    yen = len(re.findall(r"\d{1,3}(?:,\d{3})*(?:千円|万円|円)", full_text))
    year = len(re.findall(r"20\d{2}年(?:度)?", full_text))

    score = min(15, pct + yen + max(0, year - 2))
    return Signal(
        name="具体数値密度",
        weight=15,
        score=score,
        note=f"%数値 {pct} / 金額 {yen} / 年度参照 {year}",
        weak_section="section_1_2" if score < 8 else None,
    )


def _signal_length_compliance(profile: SubsidyProfile, story: dict[str, str]) -> Signal:
    """Up to 15 points. Per-section min/max compliance."""
    if not profile.sections:
        return Signal("文字数達成", 15, 0, "セクションなし", None)
    weakest_id: str | None = None
    weakest_delta = 0.0
    ok_count = 0
    for spec in profile.sections:
        actual = len(story.get(spec.section_id, ""))
        if spec.min_chars <= actual <= spec.max_chars:
            ok_count += 1
        else:
            delta = abs(actual - spec.target_chars) / spec.target_chars
            if delta > weakest_delta:
                weakest_delta = delta
                weakest_id = spec.section_id
    score = round(15 * ok_count / len(profile.sections))
    return Signal(
        name="文字数達成",
        weight=15,
        score=score,
        note=f"準拠 {ok_count}/{len(profile.sections)} セクション",
        weak_section=weakest_id,
    )


def _signal_visual_assets(profile: SubsidyProfile) -> Signal:
    """Up to 10 points. Charts and tables declared."""
    score = min(6, 3 * len(profile.charts)) + min(4, 2 * len(profile.tables))
    return Signal(
        name="図表配置",
        weight=10,
        score=score,
        note=f"グラフ {len(profile.charts)} 種 / 表 {len(profile.tables)} 種",
        weak_section=None,
    )


# ---------------------------------------------------------------------------
# Composite estimator
# ---------------------------------------------------------------------------


def estimate_adoption_probability(
    profile: SubsidyProfile, company: dict, story: dict[str, str]
) -> dict:
    """Returns a structured adoption-probability estimate.

    Keys:
      total:               int 0-100, the headline "adoption probability"
      target:              profile.quality_score_target
      passed:              bool
      signals:             list of per-signal results (with weak_section pointers)
      worst_weak_sections: list of section_ids ranked by impact on score
    """
    signals = [
        _signal_company_specific_data(company, story),
        _signal_problem_solution_alignment(company, story),
        _signal_bonus_points_usage(company, story),
        _signal_specific_numbers(story),
        _signal_length_compliance(profile, story),
        _signal_visual_assets(profile),
    ]

    total = sum(s.score for s in signals)
    target = profile.quality_score_target

    # Rank weak sections by how much room each signal has to recover
    weak_candidates: list[tuple[int, str]] = []
    for s in signals:
        if s.weak_section and s.score < s.weight:
            weak_candidates.append((s.weight - s.score, s.weak_section))
    weak_candidates.sort(reverse=True)
    worst_weak_sections: list[str] = []
    for _gap, sec in weak_candidates:
        if sec not in worst_weak_sections:
            worst_weak_sections.append(sec)

    return {
        "total": total,
        "target": target,
        "passed": total >= target,
        "signals": [
            {
                "name": s.name,
                "score": s.score,
                "weight": s.weight,
                "note": s.note,
                "weak_section": s.weak_section,
            }
            for s in signals
        ],
        "worst_weak_sections": worst_weak_sections,
    }


def format_estimate_block(report: dict) -> str:
    """Render the adoption estimate as a paragraph block for the docx."""
    lines = [
        f"採択確率（推定）: {report['total']} / 100  "
        f"（目標 {report['target']} 点）— "
        f"{'達成' if report['passed'] else '未達'}",
    ]
    for s in report["signals"]:
        flag = "✓" if s["score"] >= s["weight"] * 0.7 else "△"
        lines.append(f"  {flag} {s['name']}: {s['score']}/{s['weight']}点 — {s['note']}")
    if report["worst_weak_sections"]:
        lines.append("")
        lines.append("改善余地のあるセクション: " + ", ".join(report["worst_weak_sections"]))
    return "\n".join(lines)
