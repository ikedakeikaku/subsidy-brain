"""Get a usable .docx template for a SubsidyProfile.

Three strategies, tried in order:

  1. **Use cached official forms** — if ``GuidelineFetcher`` previously
     downloaded the publishing body's actual 様式.docx, point at that.
  2. **Look in templates/<program_id>/** — supports the optional hand-
     curated case where the user committed an official file locally.
  3. **Synthesise from profile** — build a docx skeleton with
     ``{{section_id}}`` placeholders matching every section the profile
     declares. This is always available, never depends on the network.

Returning the path to a real .docx means the downstream
``tools.template_filler.fill_template`` (placeholder substitution) and
``tools.document_assembler.assemble_document`` can use the same logic
regardless of where the template came from.
"""
from __future__ import annotations

import logging
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

from schemas.subsidy_profile import SubsidyProfile

logger = logging.getLogger(__name__)


class TemplateSynthesizer:
    """Materialise a .docx template for a given profile."""

    agent_id = "#template"
    agent_name = "TemplateSynthesizer"

    def get_template(
        self,
        profile: SubsidyProfile,
        *,
        fetched_form_paths: dict[str, str] | None = None,
        templates_root: Path | str = "templates",
    ) -> Path:
        """Return a usable template path. Generates one if needed.

        ``fetched_form_paths`` is the dict ``GuidelineFetcher`` returns for
        the downloaded official 様式 files (e.g. ``{"様式2": "/path/to/様式2.docx"}``).
        If 様式2 is present and non-empty, we use it directly.
        """
        # 1. Use the official 様式 if it actually downloaded
        if fetched_form_paths:
            for form_id, path in fetched_form_paths.items():
                p = Path(path)
                if p.exists() and p.stat().st_size > 0 and p.suffix.lower() == ".docx":
                    if "様式2" in form_id or "経営計画" in form_id or "事業計画" in form_id:
                        logger.info(
                            "TemplateSynthesizer: using official form %s -> %s",
                            form_id,
                            p,
                        )
                        return p

        # 2. Look in templates/<program_id>/
        root = Path(templates_root) / profile.program_id
        if root.exists():
            for cand in root.glob("様式*.docx"):
                return cand
            for cand in root.glob("*.docx"):
                return cand

        # 3. Synthesise a template from the profile
        out = Path(templates_root) / profile.program_id / "_synthesized.docx"
        return self._synthesise(profile, out)

    # ------------------------------------------------------------------

    @staticmethod
    def _synthesise(profile: SubsidyProfile, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)

        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "Hiragino Sans"
        style.font.size = Pt(10.5)

        section = doc.sections[0]
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)

        title = doc.add_heading(f"{profile.canonical_name} 申請書", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        meta = doc.add_paragraph()
        meta.add_run(
            "※ 本テンプレートは subsidy-brain による自動生成です。"
            "実申請には公式 様式 への移植を行ってください。"
        )

        # Applicant header
        doc.add_heading("申請者情報", level=1)
        hdr = doc.add_table(rows=4, cols=2)
        hdr.style = "Light Grid Accent 1"
        hdr.rows[0].cells[0].text = "事業者名"
        hdr.rows[0].cells[1].text = "{{ applicant_name }}"
        hdr.rows[1].cells[0].text = "代表者氏名"
        hdr.rows[1].cells[1].text = "{{ representative }}"
        hdr.rows[2].cells[0].text = "事業実施場所"
        hdr.rows[2].cells[1].text = "{{ business_address }}"
        hdr.rows[3].cells[0].text = "従業員数"
        hdr.rows[3].cells[1].text = "{{ employee_count }}"

        # One Heading + body per profile section
        for spec in profile.sections:
            doc.add_heading(spec.display_name, level=1)
            doc.add_paragraph(f"{{{{ {spec.section_id} }}}}")

        doc.save(str(out_path))
        return out_path
