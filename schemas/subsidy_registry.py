"""Subsidy program registry — the catalog of subsidies the system can target.

A program declares:
  - stable identifiers (id, round)
  - canonical name and the publishing body
  - URLs for the guideline PDF and the official Word forms (様式)
  - hard constraints (deadline, max award, subsidy rate)

The registry itself is a Protocol so the lookup layer can be backed by:
  - a hand-curated YAML / JSON (the public build's default)
  - a private DB connected to the firm's own subsidy library
  - a live scrape of jGrants / 補助金ポータル
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml
from pydantic import BaseModel, Field, HttpUrl


class SubsidyForm(BaseModel):
    """A single 様式 document distributed with a subsidy."""

    form_id: str = Field(description="Stable form identifier (e.g. '様式2')")
    name: str = Field(description="Human-readable name (e.g. '経営計画書')")
    url: HttpUrl | None = Field(
        default=None, description="Direct download URL for the .docx / .pdf"
    )
    local_path: str = Field(
        default="",
        description="Path under templates/ once the form has been fetched and cached",
    )


class SubsidyProgram(BaseModel):
    """One subsidy programme as published by a Japanese public body."""

    program_id: str = Field(description="Stable id, e.g. 'jizoku_19'")
    round_number: int = Field(default=0, description="公募回次（第N回）")
    canonical_name: str = Field(description="Canonical Japanese name")
    short_name: str = Field(default="", description="Shorthand name for prompts")
    issuing_body: str = Field(
        description="Publishing organisation (e.g. 中小企業庁・全国商工会連合会)"
    )
    landing_url: HttpUrl | None = Field(
        default=None, description="Main landing page"
    )
    guideline_pdf_url: HttpUrl | None = Field(
        default=None, description="Direct URL to the guideline PDF"
    )
    application_deadline: date | None = Field(default=None)
    max_award_yen: int = Field(default=0)
    subsidy_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Subsidy ratio, 0.66 = 2/3"
    )
    forms: list[SubsidyForm] = Field(default_factory=list)
    additional_documents: list[SubsidyForm] = Field(
        default_factory=list,
        description=(
            "Supplementary documents that aren't 様式 themselves but help the"
            " synthesiser: 入力ガイド, 記載例, FAQ, 補助事業の手引き 等"
        ),
    )
    keywords_for_research: list[str] = Field(
        default_factory=list,
        description="Search keywords used by AdoptionResearcher",
    )


@runtime_checkable
class SubsidyRegistry(Protocol):
    """Subsidy lookup contract."""

    def get(self, program_id: str) -> SubsidyProgram | None: ...

    def search(self, query: str) -> list[SubsidyProgram]: ...

    def list_all(self) -> list[SubsidyProgram]: ...


class YamlSubsidyRegistry:
    """File-backed registry. One JSON / YAML file lists every programme.

    Programmes are loaded eagerly on construction so lookups are cheap, but
    the file can be edited by hand without restart (call ``reload()``).
    """

    def __init__(self, source: str | Path) -> None:
        self._source = Path(source)
        self._programs: dict[str, SubsidyProgram] = {}
        self.reload()

    def reload(self) -> None:
        if not self._source.exists():
            self._programs = {}
            return
        text = self._source.read_text(encoding="utf-8")
        if self._source.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        programs = data.get("programs", []) if isinstance(data, dict) else data
        self._programs = {}
        for raw in programs or []:
            prog = SubsidyProgram.model_validate(raw)
            self._programs[prog.program_id] = prog

    def get(self, program_id: str) -> SubsidyProgram | None:
        return self._programs.get(program_id)

    def search(self, query: str) -> list[SubsidyProgram]:
        q = query.strip()
        if not q:
            return []
        hits: list[SubsidyProgram] = []
        for prog in self._programs.values():
            haystack = " ".join(
                [
                    prog.program_id,
                    prog.canonical_name,
                    prog.short_name,
                    prog.issuing_body,
                ]
            )
            if q in haystack:
                hits.append(prog)
        return hits

    def list_all(self) -> list[SubsidyProgram]:
        return list(self._programs.values())
