"""#13 申請書組立 入出力スキーマ

ストーリー・経費・市場分析等を統合してWord申請書を生成する。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ============================================================
# Supporting models
# ============================================================


class DocumentSection(BaseModel):
    """申請書のセクション情報"""

    section_name: str = Field(..., description="セクション名")
    char_count: int = Field(default=0, description="文字数")
    char_limit: int = Field(default=0, description="文字数上限")
    has_charts: bool = Field(default=False, description="グラフ・図表を含むか")


class GeneratedDocument(BaseModel):
    """生成された申請書ドキュメント"""

    doc_type: str = Field(..., description="経営計画書 | 補助事業計画書")
    file_path: str = Field(default="", description="生成ファイルのパス")
    sections: list[DocumentSection] = Field(
        default_factory=list, description="セクション一覧"
    )


class ExpenseTableOutput(BaseModel):
    """経費明細テーブル出力"""

    file_path: str = Field(default="", description="経費明細ファイルのパス")
    items: list[dict] = Field(default_factory=list, description="経費明細行一覧")


class DocumentMetadata(BaseModel):
    """ドキュメントメタデータ"""

    total_pages: int = Field(default=0, description="総ページ数")
    generated_at: str = Field(default="", description="生成日時（ISO8601）")
    version: int = Field(default=1, description="ドキュメントバージョン")


class FundingSources(BaseModel):
    """補助事業の資金調達内訳"""

    total_project_cost: int = Field(
        default=0, description="補助事業費合計（円）"
    )
    subsidy_amount: int = Field(
        default=0, description="補助金申請額（円）"
    )
    self_funding: int = Field(
        default=0, description="自己負担額（円）= 補助事業費合計 - 補助金申請額"
    )
    subsidy_rate: float = Field(
        default=0.0,
        description="補助率（例: 0.667 = 2/3）。subsidy_amount / total_project_cost で算出",
    )
    loan_amount: int = Field(
        default=0, description="借入予定額（円）。自己負担の一部を借入で賄う場合"
    )
    note: str = Field(
        default="", description="資金調達に関する補足事項"
    )


class SubsidyCalculation(BaseModel):
    """ウェブサイト関連費の補助金計算（上限・按分）

    ウェブサイト関連費は補助金総額の1/4が上限（令和7年度以降の制度変更対応）。
    この上限を超える場合、超過分は全額自己負担となる。
    """

    web_expense_total: int = Field(
        default=0, description="ウェブサイト関連費の合計見積額（円）"
    )
    other_expense_total: int = Field(
        default=0, description="ウェブサイト関連費以外の経費合計（円）"
    )
    gross_subsidy_before_cap: int = Field(
        default=0,
        description="上限適用前の補助金申請額（円）= (web + other) × 補助率",
    )
    web_subsidy_cap: int = Field(
        default=0,
        description="ウェブサイト関連費の補助金上限額（円）= 補助金総額 × 1/4",
    )
    web_subsidy_applied: int = Field(
        default=0,
        description="実際に適用されるウェブサイト関連費の補助金額（円）。上限以下に丸め済み",
    )
    other_subsidy_applied: int = Field(
        default=0, description="ウェブサイト関連費以外の補助金額（円）"
    )
    final_subsidy_amount: int = Field(
        default=0,
        description="最終的な補助金申請額（円）= web_subsidy_applied + other_subsidy_applied",
    )
    is_web_capped: bool = Field(
        default=False,
        description="ウェブサイト関連費が上限に達して按分が発生したか",
    )
    cap_note: str = Field(
        default="",
        description="上限・按分に関する補足説明（申請書の経費明細の注記として使用）",
    )


# ============================================================
# Input / Output
# ============================================================


class DocumentBuildInput(BaseModel):
    """#13 申請書組立入力"""

    story: dict = Field(default_factory=dict, description="#8 ストーリー出力")
    expenses: dict = Field(default_factory=dict, description="#9 経費サマリー出力")
    guideline_data: dict = Field(
        default_factory=dict, description="#5 要領パーサー出力"
    )
    financial_data: dict = Field(
        default_factory=dict, description="#6 決算書読込出力"
    )
    hearing_data: dict = Field(
        default_factory=dict, description="ヒアリングデータ（企業概要・強み等）"
    )
    market_analysis: dict | None = Field(
        default=None, description="#10 市場分析出力（Phase 3）"
    )
    financial_projections: dict | None = Field(
        default=None, description="#11 財務効果出力（Phase 3）"
    )
    fact_check_results: dict | None = Field(
        default=None, description="#12 ファクトチェック出力（Phase 3）"
    )
    charts: list[str] = Field(
        default_factory=list, description="グラフ画像パス一覧"
    )
    template_id: str = Field(
        default="application_form", description="テンプレートID"
    )
    revision_instructions: list[str] = Field(
        default_factory=list, description="修正指示一覧"
    )
    applicant_id: str = Field(default="", description="申請者ID")


class DocumentBuildOutput(BaseModel):
    """#13 申請書組立出力"""

    documents: list[GeneratedDocument] = Field(
        default_factory=list, description="生成ドキュメント一覧"
    )
    expense_table: ExpenseTableOutput | None = Field(
        default=None, description="経費明細テーブル"
    )
    funding_sources: FundingSources | None = Field(
        default=None, description="資金調達内訳（申請書の資金調達欄に使用）"
    )
    subsidy_calculation: SubsidyCalculation | None = Field(
        default=None,
        description="ウェブサイト関連費の補助金計算結果（上限・按分の詳細）",
    )
    metadata: DocumentMetadata = Field(
        default_factory=DocumentMetadata, description="メタデータ"
    )
