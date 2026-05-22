"""#13 DocumentBuilder — profile-driven document assembly.

Replaces the original 持続化補助金-only implementation. The new builder is
a thin wrapper that:

  1. Loads the ``SubsidyProfile`` for the target subsidy.
  2. Delegates Word assembly to ``tools.document_assembler``.
  3. Delegates Excel template filling (if any 様式 is xlsx) to
     ``tools.xlsx_filler`` with format preservation.

All section ordering, target word counts, chart placement, and table
shapes are declared in the profile YAML — never hardcoded here. The same
agent therefore handles 持続化補助金, ものづくり補助金, 省力化投資補助金,
IT導入補助金, 事業再構築補助金 and so on with zero code change; only
new ``presets/<id>.yaml`` + ``presets/<id>_profile.yaml`` are needed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from schemas.document_build import (
    DocumentBuildInput,
    DocumentBuildOutput,
    DocumentMetadata,
    DocumentSection,
    GeneratedDocument,
)
from schemas.subsidy_profile import SubsidyProfile, load_profile
from tools.document_assembler import assemble_document

logger = logging.getLogger(__name__)


class DocumentBuilder(BaseAgent):
    """Profile-driven application document builder."""

    agent_id = "#13"
    agent_name = "申請書組立"

    async def _execute_impl(
        self, input_data: DocumentBuildInput
    ) -> DocumentBuildOutput:
        profile = self._resolve_profile(input_data)
        out_path = self._resolve_out_path(input_data, profile)

        company = _merge_company(input_data)
        story = dict(input_data.story or {})

        assemble_report = assemble_document(
            profile=profile,
            company=company,
            story=story,
            out_path=out_path,
            extra_metadata={
                "applicant_id": input_data.applicant_id or "",
                "subsidy": profile.canonical_name,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            quality_block=None,
        )

        excel_outputs = self._fill_excel_forms(input_data, profile)

        sections = [
            self._section_meta(profile, sid, story.get(sid, ""))
            for sid in assemble_report["sections_rendered"]
        ]

        return DocumentBuildOutput(
            documents=[
                GeneratedDocument(
                    doc_type="application_form",
                    file_path=str(out_path),
                    sections=sections,
                )
            ]
            + [
                GeneratedDocument(
                    doc_type="form_xlsx",
                    file_path=str(p),
                    sections=[],
                )
                for p in excel_outputs
            ],
            expense_table=None,
            funding_sources=None,
            subsidy_calculation=None,
            metadata=DocumentMetadata(
                total_pages=len(sections),
                generated_at=datetime.now().isoformat(timespec="seconds"),
                version=1,
            ),
        )

    # ------------------------------------------------------------------

    def _resolve_profile(self, input_data: DocumentBuildInput) -> SubsidyProfile:
        """Look up the profile via ``template_id``.

        ``template_id`` can be:
          * an absolute / relative path to a profile YAML
          * a stem under ``presets/`` (e.g. ``"jizoku_19"`` →
            ``presets/jizoku_19_profile.yaml``) for users who have committed
            their own overrides

        For the natural-language workflow, callers should not use this
        agent at all — they should pass the synthesised SubsidyProfile
        directly to ``tools.document_assembler.assemble_document`` or
        ``tools.official_form_filler.fill_official_form``.
        """
        candidates: list[Path] = []
        tid = input_data.template_id or ""
        if tid:
            candidates.append(Path("presets") / f"{tid}_profile.yaml")
            candidates.append(Path("presets") / f"{tid}.yaml")
            candidates.append(Path(tid))

        for c in candidates:
            if c.exists() and c.suffix in {".yaml", ".yml"}:
                return load_profile(c)
        raise FileNotFoundError(
            f"No SubsidyProfile found for template_id={tid!r}. "
            "Either commit presets/<id>_profile.yaml or use the "
            "natural-language pipeline (run_natural_demo.py) which "
            "synthesises the profile via ProfileSynthesizer."
        )

    def _resolve_out_path(
        self, input_data: DocumentBuildInput, profile: SubsidyProfile
    ) -> Path:
        out = Path("demo") / "output" / f"{profile.program_id}_application.docx"
        out.parent.mkdir(parents=True, exist_ok=True)
        return out

    def _fill_excel_forms(
        self, input_data: DocumentBuildInput, profile: SubsidyProfile
    ) -> list[Path]:
        """If the program ships an xlsx 様式, fill it from input data."""
        from tools.xlsx_filler import fill_xlsx_template

        templates_dir = Path("templates") / profile.program_id
        if not templates_dir.exists():
            return []
        out_paths: list[Path] = []
        for path in sorted(templates_dir.glob("*.xlsx")):
            subs = _flatten_for_excel(input_data)
            target = Path("demo") / "output" / path.name
            try:
                fill_xlsx_template(path, target, subs)
                out_paths.append(target)
            except Exception as e:  # noqa: BLE001
                logger.warning("xlsx fill failed for %s: %s", path, e)
        return out_paths

    def _section_meta(
        self, profile: SubsidyProfile, section_id: str, text: str
    ) -> DocumentSection:
        spec = profile.section_by_id(section_id)
        return DocumentSection(
            section_name=spec.display_name if spec else section_id,
            char_count=len(text),
            char_limit=spec.max_chars if spec else 0,
            has_charts=any(
                c.place_after_section == section_id for c in profile.charts
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _merge_company(input_data: DocumentBuildInput) -> dict[str, Any]:
    """Combine the hearing / financial / expense dicts into the shape that
    document_assembler expects."""
    company: dict[str, Any] = {}
    if input_data.hearing_data:
        company.update(dict(input_data.hearing_data))
    if input_data.financial_data:
        company["financial"] = dict(input_data.financial_data)
    if input_data.expenses:
        company["expenses"] = dict(input_data.expenses)
    return company


def _flatten_for_excel(input_data: DocumentBuildInput) -> dict[str, str]:
    """Map hearing/expense data into the placeholders the sample xlsx uses."""
    company = (input_data.hearing_data or {}).get("company") or {}
    expenses = (input_data.expenses or {}).get("breakdown") or []
    subs: dict[str, str] = {
        "applicant_name": str(company.get("name", "")),
        "representative": str(company.get("representative", "")),
        "expense_total": f"{(input_data.expenses or {}).get('total', 0):,}",
        "subsidy_amount": f"{(input_data.expenses or {}).get('subsidy_amount', 0):,}",
        "self_funding": f"{(input_data.expenses or {}).get('self_funding', 0):,}",
    }
    for i, item in enumerate(expenses[:5], start=1):
        subs[f"expense_{i}_category"] = str(item.get("category", ""))
        subs[f"expense_{i}_item"] = str(item.get("item", ""))
        subs[f"expense_{i}_amount"] = f"{item.get('amount', 0):,}"
        subs[f"expense_{i}_note"] = str(item.get("note", ""))
    for i in range(len(expenses[:5]) + 1, 6):
        for field in ("category", "item", "amount", "note"):
            subs[f"expense_{i}_{field}"] = ""
    return subs


__all__ = ["DocumentBuilder"]
