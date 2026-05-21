"""Profile-driven section-length validator.

Given a ``SubsidyProfile`` and a story dict (section_id -> text), report:

  - per-section status: ok / underfilled / overfilled
  - delta from target
  - the worst offenders
  - overall compliance ratio

This lets the orchestrator decide whether to expand or condense a section
before the document is rendered, and lets the user see in the manifest
which sections were borderline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from schemas.subsidy_profile import SectionSpec, SubsidyProfile

Status = Literal["ok", "underfilled", "overfilled", "missing"]


@dataclass(frozen=True)
class SectionLengthReport:
    section_id: str
    display_name: str
    actual_chars: int
    target_chars: int
    min_chars: int
    max_chars: int
    status: Status
    delta_from_target: int     # +ve = overfilled, -ve = underfilled
    delta_pct: float           # signed % from target


def _classify(actual: int, spec: SectionSpec) -> Status:
    if actual == 0:
        return "missing"
    if actual < spec.min_chars:
        return "underfilled"
    if actual > spec.max_chars:
        return "overfilled"
    return "ok"


def validate_lengths(
    profile: SubsidyProfile, story: dict[str, str]
) -> dict:
    """Return a comprehensive validation report."""
    section_reports: list[SectionLengthReport] = []
    for spec in profile.sections:
        text = story.get(spec.section_id, "") or ""
        actual = len(text)
        status = _classify(actual, spec)
        delta = actual - spec.target_chars
        pct = (delta / spec.target_chars * 100) if spec.target_chars else 0.0
        section_reports.append(
            SectionLengthReport(
                section_id=spec.section_id,
                display_name=spec.display_name,
                actual_chars=actual,
                target_chars=spec.target_chars,
                min_chars=spec.min_chars,
                max_chars=spec.max_chars,
                status=status,
                delta_from_target=delta,
                delta_pct=round(pct, 1),
            )
        )

    total_actual = sum(r.actual_chars for r in section_reports)
    total_target = profile.total_target_chars
    ok_count = sum(1 for r in section_reports if r.status == "ok")
    compliance = round(ok_count / len(section_reports) * 100, 1) if section_reports else 0.0

    worst = sorted(
        (r for r in section_reports if r.status != "ok"),
        key=lambda r: abs(r.delta_pct),
        reverse=True,
    )[:3]

    return {
        "compliance_pct": compliance,
        "total_actual_chars": total_actual,
        "total_target_chars": total_target,
        "section_count": len(section_reports),
        "ok_count": ok_count,
        "sections": [r.__dict__ for r in section_reports],
        "worst_offenders": [
            {
                "section_id": r.section_id,
                "display_name": r.display_name,
                "status": r.status,
                "actual_chars": r.actual_chars,
                "target_chars": r.target_chars,
                "delta_pct": r.delta_pct,
            }
            for r in worst
        ],
    }
