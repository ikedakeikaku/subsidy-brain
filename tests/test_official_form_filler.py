"""Tests for OfficialFormFiller against a programmatically built mock
"official-style" template.

Real 様式 docx files distributed by Japanese subsidy bodies don't carry
``{{placeholder}}`` markers; they're shipped as form-style documents
with headings and empty body space. These tests build a doc that matches
that shape on the fly (no committed sample files), then verify the
filler covers section bodies, applicant info cells, appended profile
tables, and appended profile charts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _build_official_style_template(out: Path) -> Path:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Hiragino Sans"
    style.font.size = Pt(10.5)
    doc.sections[0].left_margin = Cm(2.5)

    title = doc.add_heading("様式２（経営計画書 兼 補助事業計画書）", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    tbl = doc.add_table(rows=4, cols=2)
    tbl.style = "Light Grid Accent 1"
    for i, label in enumerate(
        ["事業者名", "代表者氏名", "事業実施場所", "従業員数"]
    ):
        tbl.rows[i].cells[0].text = label
        tbl.rows[i].cells[1].text = ""

    doc.add_heading("＜加点項目＞", level=1)
    doc.add_heading("【重点政策加点】事業環境変化加点", level=2)
    doc.add_paragraph()
    doc.add_paragraph()

    for heading in [
        "1-1. 自社の概要",
        "1-2. 売上・利益の状況",
        "1-3. 経営課題",
        "2-1. 市場の動向",
        "3. 強み・弱み",
        "4-2. 今後のプラン",
        "補助事業の効果",
    ]:
        doc.add_heading(heading, level=2)
        doc.add_paragraph()

    doc.save(str(out))
    return out


def test_official_form_filler_fills_every_section(
    tmp_path: Path, sample_profile, sample_company
) -> None:
    from demo.mock_story import MOCK_STORY
    from tools.official_form_filler import fill_official_form

    template = _build_official_style_template(tmp_path / "様式2.docx")
    out = tmp_path / "filled.docx"

    report = fill_official_form(
        template_path=template,
        out_path=out,
        profile=sample_profile,
        story=MOCK_STORY,
        company=sample_company,
    )

    assert report["sections_not_found"] == [], report
    assert set(report["sections_filled"]) == {
        s.section_id for s in sample_profile.sections
    }
    assert report["applicant_cells_filled"] == 4
    assert "table_schedule" in report["tables_appended"]
    assert "table_expense" in report["tables_appended"]
    assert "chart_revenue_trend" in report.get("charts_appended", [])
    assert out.stat().st_size > template.stat().st_size


def test_official_form_filler_preserves_template_text(
    tmp_path: Path, sample_profile, sample_company
) -> None:
    from demo.mock_story import MOCK_STORY
    from tools.official_form_filler import fill_official_form

    template = _build_official_style_template(tmp_path / "様式2.docx")
    out = tmp_path / "filled.docx"
    fill_official_form(
        template_path=template,
        out_path=out,
        profile=sample_profile,
        story=MOCK_STORY,
        company=sample_company,
    )

    filled = Document(str(out))
    text_blob = "\n".join(p.text for p in filled.paragraphs)
    assert "様式２" in text_blob
    assert "1-1. 自社の概要" in text_blob
    assert "4-2. 今後のプラン" in text_blob

    labels_found = set()
    for table in filled.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip() in {
                    "事業者名",
                    "代表者氏名",
                    "事業実施場所",
                    "従業員数",
                }:
                    labels_found.add(cell.text.strip())
    assert labels_found >= {"事業者名", "代表者氏名"}


def test_section_signature_matches_variant_headings() -> None:
    from tools.official_form_filler import _section_signature

    a = _section_signature("4-2. 今後のプラン")
    b = _section_signature("4-2. 今後のプラン（施策）")
    assert a[0] == b[0] == "4-2"

    bonus_a = _section_signature("【重点政策加点】事業環境変化加点")
    bonus_b = _section_signature("【加点】事業環境変化")
    assert bonus_a[0] == bonus_b[0] == "bonus:env_change"
