"""Light-weight quality scoring driven by the profile.

The full QualityChecker agent (#14) is heavy because it round-trips Claude
for rubric checks. For the public demo we use a fast, deterministic scorer
that gives the user a realistic-looking but reproducible 0–100 score
broken down across four standard subsidy review axes. When the live Claude
path is used, this scorer's output is augmented but not replaced.
"""
from __future__ import annotations

from schemas.subsidy_profile import SubsidyProfile


def _axis_length(profile: SubsidyProfile, story: dict[str, str]) -> tuple[int, str]:
    """Up to 25 points for hitting per-section length targets."""
    if not profile.sections:
        return 0, "セクションなし"
    score = 0.0
    for spec in profile.sections:
        actual = len(story.get(spec.section_id, ""))
        if actual >= spec.min_chars and actual <= spec.max_chars:
            score += 25 / len(profile.sections)
        elif actual >= spec.min_chars * 0.7:
            score += 12 / len(profile.sections)
    return round(score), f"文字数達成: {round(score)}/25"


def _axis_data_specificity(company: dict, story: dict[str, str]) -> tuple[int, str]:
    """Up to 25 points for embedding specific numbers in the story."""
    score = 0
    notes: list[str] = []
    full_text = "\n".join(story.values())

    # Look for hard numbers (a stand-in for "concrete claims")
    import re

    pct_count = len(re.findall(r"\d+(?:\.\d+)?%", full_text))
    yen_count = len(re.findall(r"\d{1,3}(?:,\d{3})*(?:千円|万円|円)", full_text))
    year_count = len(re.findall(r"20\d{2}年(?:度)?", full_text))

    if pct_count >= 5:
        score += 8
        notes.append(f"%数値 {pct_count} 件")
    if yen_count >= 6:
        score += 9
        notes.append(f"金額 {yen_count} 件")
    if year_count >= 3:
        score += 8
        notes.append(f"年度参照 {year_count} 件")
    return score, "具体数値: " + ", ".join(notes)


def _axis_structure(profile: SubsidyProfile, story: dict[str, str]) -> tuple[int, str]:
    """Up to 25 points for covering every required section."""
    if not profile.sections:
        return 0, "セクションなし"
    covered = sum(
        1 for spec in profile.sections if story.get(spec.section_id, "").strip()
    )
    pct = covered / len(profile.sections)
    score = round(25 * pct)
    return score, f"必須セクション充足: {covered}/{len(profile.sections)}"


def _axis_visual_assets(profile: SubsidyProfile) -> tuple[int, str]:
    """Up to 25 points for declaring required charts and tables."""
    score = 0
    notes: list[str] = []
    if profile.charts:
        score += min(15, 5 * len(profile.charts))
        notes.append(f"グラフ {len(profile.charts)} 種")
    if profile.tables:
        score += min(10, 3 * len(profile.tables))
        notes.append(f"表 {len(profile.tables)} 種")
    return score, "図表配置: " + (", ".join(notes) or "なし")


def score_application(
    profile: SubsidyProfile, company: dict, story: dict[str, str]
) -> dict:
    axes = {
        "length": _axis_length(profile, story),
        "data_specificity": _axis_data_specificity(company, story),
        "structure": _axis_structure(profile, story),
        "visual_assets": _axis_visual_assets(profile),
    }
    total = sum(score for score, _note in axes.values())
    return {
        "total": total,
        "target": profile.quality_score_target,
        "passed": total >= profile.quality_score_target,
        "breakdown": {
            name: {"score": score, "note": note}
            for name, (score, note) in axes.items()
        },
    }


def format_quality_block(report: dict) -> str:
    """Render the score as a paragraph string for embedding in the .docx."""
    lines = [
        f"総合スコア: {report['total']} / 100点（目標 {report['target']} 点）"
        f" — {'達成' if report['passed'] else '未達'}",
    ]
    for axis_name, info in report["breakdown"].items():
        lines.append(f"  - {axis_name}: {info['score']}点 ({info['note']})")
    return "\n".join(lines)
