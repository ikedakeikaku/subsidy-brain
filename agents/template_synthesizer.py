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
    ) -> tuple[Path, str]:
        """Return ``(template_path, source)`` where ``source`` is one of:

          * ``"official"`` — file came from GuidelineFetcher / official URL
          * ``"local"`` — file was committed under ``templates/<program_id>/``
          * ``"draft"`` — a clearly-labelled DRAFT skeleton synthesised from
            the profile. Carries a warning header so it can't be mistaken
            for the publishing body's actual form.
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
                        return p, "official"

        # 2. Look in templates/<program_id>/
        root = Path(templates_root) / profile.program_id
        if root.exists():
            for cand in root.glob("様式*.docx"):
                return cand, "local"
            for cand in root.glob("*.docx"):
                if not cand.name.startswith("_"):
                    return cand, "local"

        # 3. Synthesise a DRAFT skeleton from the profile. Never claim it
        #    is the official 様式 — the synthesised file carries a warning
        #    header so reviewers immediately spot a non-official document.
        out = Path(templates_root) / profile.program_id / "_draft_skeleton.docx"
        return self._synthesise(profile, out), "draft"

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

        # Warning header — never let this skeleton be confused for the
        # publishing body's official 様式.
        warn = doc.add_paragraph()
        warn_run = warn.add_run(
            "【DRAFT — 本ファイルは公式 様式 ではありません】"
        )
        warn_run.bold = True
        doc.add_paragraph(
            "subsidy-brain が公募要領を読み取って合成した草稿スケルトンです。"
            "実申請時は publishing body（中小企業庁・全国商工会連合会 等）が"
            "配布する公式 様式 docx を取得し、本文だけを移植してください。"
            "本ファイルの罫線・ヘッダ・ページ設定は公式 様式 とは一致しません。"
        )

        title = doc.add_heading(
            f"{profile.canonical_name} 申請書（DRAFT SKELETON）", level=0
        )
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

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
