"""Tests for the profile-driven length / scoring / assembly stack."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def test_profile_loads_with_charts_and_tables() -> None:
    from schemas.subsidy_profile import ChartType, SubsidyProfile, TableType, load_profile

    profile: SubsidyProfile = load_profile(ROOT / "demo" / "sample_profile.yaml")
    assert profile.program_id == "sample_hanro_kaitaku_v1"
    assert len(profile.sections) == 8
    assert profile.total_target_chars == 5_700

    chart_types = {c.chart_type for c in profile.charts}
    assert ChartType.REVENUE_TREND in chart_types
    assert ChartType.EFFECT_BEFORE_AFTER in chart_types

    table_types = {t.table_type for t in profile.tables}
    assert TableType.SCHEDULE in table_types
    assert TableType.EXPENSE_BREAKDOWN in table_types


# ---------------------------------------------------------------------------
# Length validator
# ---------------------------------------------------------------------------


def test_length_validator_flags_underfilled_sections() -> None:
    from schemas.subsidy_profile import load_profile
    from tools.length_validator import validate_lengths

    profile = load_profile(ROOT / "demo" / "sample_profile.yaml")
    report = validate_lengths(profile, {"section_1_1": "短い"})
    # All other sections are missing — compliance should be very low.
    assert report["compliance_pct"] < 20.0
    statuses = {s["section_id"]: s["status"] for s in report["sections"]}
    assert statuses["section_1_1"] == "underfilled"
    assert statuses["section_1_2"] == "missing"
    assert statuses["section_4_2"] == "missing"


def test_full_mock_story_passes_compliance() -> None:
    from demo.mock_story import MOCK_STORY
    from schemas.subsidy_profile import load_profile
    from tools.length_validator import validate_lengths

    profile = load_profile(ROOT / "demo" / "sample_profile.yaml")
    report = validate_lengths(profile, MOCK_STORY)
    assert report["compliance_pct"] >= 90.0, report["worst_offenders"]
    assert report["total_actual_chars"] >= profile.total_min_chars


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------


def test_quality_score_breakdown_has_four_axes() -> None:
    from demo.mock_story import MOCK_STORY
    from schemas.subsidy_profile import load_profile
    from tools.quality_scoring import score_application

    company = yaml.safe_load(
        (ROOT / "demo" / "sample_company.yaml").read_text(encoding="utf-8")
    )
    profile = load_profile(ROOT / "demo" / "sample_profile.yaml")
    report = score_application(profile, company, MOCK_STORY)
    assert set(report["breakdown"]) == {
        "length",
        "data_specificity",
        "structure",
        "visual_assets",
    }
    assert 0 <= report["total"] <= 100
    # Mock story is well-formed enough to clear the profile's target.
    assert report["passed"] is True


# ---------------------------------------------------------------------------
# Document assembler
# ---------------------------------------------------------------------------


def test_assembler_inserts_charts_and_tables(tmp_path: Path) -> None:
    from demo.mock_story import MOCK_STORY
    from schemas.subsidy_profile import load_profile
    from tools.document_assembler import assemble_document

    company = yaml.safe_load(
        (ROOT / "demo" / "sample_company.yaml").read_text(encoding="utf-8")
    )
    profile = load_profile(ROOT / "demo" / "sample_profile.yaml")
    out = tmp_path / "demo.docx"

    report = assemble_document(
        profile=profile,
        company=company,
        story=MOCK_STORY,
        out_path=out,
        extra_metadata={"test": "yes"},
        quality_block="score: test",
    )

    assert out.exists()
    assert out.stat().st_size > 50_000  # docx with embedded PNGs is sizeable
    assert "chart_revenue_trend" in report["charts_inserted"]
    assert "chart_effect_before_after" in report["charts_inserted"]
    assert "table_schedule" in report["tables_inserted"]
    assert "table_expense" in report["tables_inserted"]
    assert len(report["sections_rendered"]) == 8
