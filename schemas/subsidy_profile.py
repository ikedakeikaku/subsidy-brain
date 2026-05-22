"""Subsidy profile — declarative structure that drives length, charts, tables.

Different subsidies have different shapes:

  - 持続化補助金:        ~8–10ページ、各セクション 600–1,500字
  - ものづくり補助金:    ~10–15ページ、より詳細
  - IT導入補助金:        テーブル中心
  - 事業再構築補助金:    ~15–20ページ規模

Rather than write a per-subsidy Python file, every subsidy declares its
shape in this Pydantic model. A single set of generic engines
(LengthValidator, ChartInserter, TableGenerator) consumes the profile and
produces the right output for whichever subsidy is being applied to.

A profile is loadable from YAML / JSON next to the registry entry so
non-engineers can add new subsidies without touching code.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from schemas.bonus_item import BonusItemSpec


class ChartType(str, Enum):
    REVENUE_TREND = "revenue_trend"            # 月次/年次の売上推移
    REVENUE_BREAKDOWN = "revenue_breakdown"    # 売上構成比（円グラフ）
    EFFECT_BEFORE_AFTER = "effect_before_after"  # 補助事業効果のbefore/after
    KPI_BAR = "kpi_bar"                        # 施策別KPI（横棒）


class TableType(str, Enum):
    PL_HISTORY = "pl_history"                  # 過去N期P/L
    EXPENSE_BREAKDOWN = "expense_breakdown"    # 経費明細
    SCHEDULE = "schedule"                      # スケジュール表
    KPI_TABLE = "kpi_table"                    # KPI 表


class SectionSpec(BaseModel):
    """One section of the application form."""

    section_id: str = Field(description="Placeholder key, e.g. 'section_1_1'")
    display_name: str = Field(description="日本語見出し（例: '1-1. 自社の概要'）")
    target_chars: int = Field(description="目標文字数（採点的に効く目安）")
    min_chars: int = Field(description="最低字数（これを下回ると審査で不利）")
    max_chars: int = Field(description="最大字数（公募要領で定められた上限）")
    requires_data_paths: list[str] = Field(
        default_factory=list,
        description="このセクションが要求するデータパス（例: 'financial.past_3y_pl'）",
    )
    bullet_count_hint: int = Field(
        default=0,
        description="箇条書きの推奨数（0なら段落形式）",
    )
    kind: str = Field(
        default="section",
        description=(
            "見出しの種別。'leaf' = 文字数制限が明示された記入対象、"
            "'container' = 配下に leaf を持つ見出し（本文は不要）、"
            "'section' = 旧来のトップレベル節（document_assembler のデフォルト）"
        ),
    )


class ChartSpec(BaseModel):
    """A chart that must appear in the application."""

    chart_id: str = Field(description="Placeholder key, e.g. 'chart_revenue_trend'")
    chart_type: ChartType
    title: str
    data_path: str = Field(description="データ取得元のパス（例: 'financial.past_3y_pl'）")
    place_after_section: str = Field(description="このセクションIDの直後に挿入")
    width_cm: float = Field(default=14.0)


class TableSpec(BaseModel):
    """A table that must appear in the application."""

    table_id: str
    table_type: TableType
    title: str
    columns: list[str]
    data_path: str
    place_after_section: str


class SubsidyProfile(BaseModel):
    """Complete declarative profile for one subsidy program."""

    program_id: str
    canonical_name: str
    sections: list[SectionSpec]
    charts: list[ChartSpec] = Field(default_factory=list)
    tables: list[TableSpec] = Field(default_factory=list)
    bonus_items: list[BonusItemSpec] = Field(
        default_factory=list,
        description=(
            "この補助金固有の加点項目。空なら『加点項目なし』を意味する（補助金"
            "ごとに加点項目は異なる）"
        ),
    )
    quality_score_target: int = Field(
        default=80, description="自己採点の到達目標（100点満点）"
    )

    @property
    def total_target_chars(self) -> int:
        return sum(s.target_chars for s in self.sections)

    @property
    def total_min_chars(self) -> int:
        return sum(s.min_chars for s in self.sections)

    @property
    def total_max_chars(self) -> int:
        return sum(s.max_chars for s in self.sections)

    def section_by_id(self, section_id: str) -> SectionSpec | None:
        for s in self.sections:
            if s.section_id == section_id:
                return s
        return None


def load_profile(path: str | Path) -> SubsidyProfile:
    """Load a profile from YAML / JSON."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if p.suffix.lower() in {".yaml", ".yml"} else None
    if data is None:
        import json

        data = json.loads(text)
    return SubsidyProfile.model_validate(data)
