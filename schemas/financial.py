"""#6 決算書読込 入出力スキーマ

決算書（青色申告・freee・法人PL/BS）を読み取り、財務データを構造化する。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Supporting models ---


class OperatingExpenses(BaseModel):
    """販管費の内訳"""

    total: float = Field(default=0, description="販管費合計")
    breakdown: dict[str, float] = Field(
        default_factory=dict, description="費目別内訳（例: {'人件費': 1000000}）"
    )


class PLSummary(BaseModel):
    """損益計算書サマリー"""

    revenue: float = Field(default=0, description="売上高")
    cost_of_sales: float = Field(default=0, description="売上原価")
    gross_profit: float = Field(default=0, description="粗利益")
    operating_expenses: OperatingExpenses = Field(
        default_factory=OperatingExpenses, description="販管費"
    )
    operating_income: float = Field(default=0, description="営業利益")
    net_income: float = Field(default=0, description="当期純利益")


class BSSummary(BaseModel):
    """貸借対照表サマリー"""

    total_assets: float = Field(default=0, description="資産合計")
    total_liabilities: float = Field(default=0, description="負債合計")
    net_assets: float = Field(default=0, description="純資産")


class DerivedMetrics(BaseModel):
    """導出された財務指標"""

    gross_margin: float | None = Field(default=None, description="粗利率")
    operating_margin: float | None = Field(default=None, description="営業利益率")
    yoy_revenue_growth: float | None = Field(default=None, description="前年比売上成長率")


class RawLineItem(BaseModel):
    """読み取った個別勘定科目"""

    name: str = Field(..., description="勘定科目名")
    amount: float = Field(..., description="金額")
    category: str = Field(default="", description="分類")


class PageMap(BaseModel):
    """PDFページマッピング"""

    pl_pages: list[int] = Field(default_factory=list, description="PL該当ページ")
    bs_pages: list[int] = Field(default_factory=list, description="BS該当ページ")
    tax_return_pages: list[int] = Field(
        default_factory=list, description="確定申告書該当ページ"
    )
    total_pages: int = Field(default=0, description="総ページ数")


# --- Input / Output ---


class FinancialReaderInput(BaseModel):
    """#6 決算書読込入力"""

    document_type: str = Field(
        ...,
        description="blue_return | freee_pl | corporate_pl | corporate_bs",
    )
    file_data: str = Field(..., description="Base64エンコードされたファイル")
    fiscal_year: str = Field(..., description="会計年度（例: 2024）")
    applicant_id: str = Field(..., description="申請者ID")


class FinancialReaderOutput(BaseModel):
    """#6 決算書読込出力"""

    fiscal_year: str = Field(..., description="会計年度")
    pl_summary: PLSummary = Field(default_factory=PLSummary, description="PL要約")
    bs_summary: BSSummary | None = Field(default=None, description="BS要約（法人のみ）")
    derived_metrics: DerivedMetrics = Field(
        default_factory=DerivedMetrics, description="導出指標"
    )
    raw_line_items: list[RawLineItem] = Field(
        default_factory=list, description="個別勘定科目一覧"
    )
    page_map: PageMap = Field(default_factory=PageMap, description="ページマッピング")
    source_file_path: str = Field(default="", description="元ファイルの保存先パス")
