"""#5 要領パーサー 入出力スキーマ

公募要領PDFを解析し、審査基準・経費ルール・必要書類等を構造化する。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Supporting models ---


class ScoringCriterion(BaseModel):
    """審査基準の各項目"""

    category: str = Field(..., description="経営計画 | 補助事業計画 | 加点")
    item: str = Field(..., description="審査項目名")
    max_points: int | None = Field(default=None, description="配点上限")
    description: str = Field(default="", description="項目の説明")
    keywords: list[str] = Field(default_factory=list, description="関連キーワード")


class ExpenseRule(BaseModel):
    """経費に関するルール定義"""

    rule_id: str = Field(..., description="ルール識別子")
    category: str = Field(..., description="経費カテゴリ（ウェブサイト関連費 等）")
    rule_type: str = Field(
        ..., description="ratio_limit | absolute_limit | requires_quote | excluded"
    )
    parameters: dict = Field(default_factory=dict, description="ルールのパラメータ")
    description: str = Field(default="", description="ルールの説明")


class RequiredDocument(BaseModel):
    """必要書類の定義"""

    name: str = Field(..., description="書類名")
    conditions: str = Field(default="", description="提出条件")
    format: str = Field(default="", description="書式・フォーマット指定")


class NumericalRequirements(BaseModel):
    """補助率・上限額等の数値要件"""

    subsidy_rate: float = Field(default=0.667, description="補助率")
    max_amount: int = Field(default=500000, description="補助上限額（通常枠）")
    special_max_amount: dict[str, int] = Field(
        default_factory=dict, description="特別枠ごとの上限額"
    )


class Deadlines(BaseModel):
    """申請期限"""

    application: str | None = Field(default=None, description="申請締切（ISO8601）")
    additional: dict[str, str] = Field(
        default_factory=dict, description="その他期限（名称: ISO8601）"
    )


# --- Input / Output ---


class GuidelineParserInput(BaseModel):
    """#5 要領パーサー入力"""

    guideline_pdf: str = Field(..., description="Base64エンコードされたPDF")
    subsidy_name: str = Field(
        default="小規模事業者持続化補助金", description="補助金名称"
    )
    submission_round: str = Field(..., description="第XX回")
    url: str | None = Field(default=None, description="公募要領のURL")


class GuidelineParserOutput(BaseModel):
    """#5 要領パーサー出力"""

    subsidy_id: str = Field(..., description="補助金識別子")
    scoring_criteria: list[ScoringCriterion] = Field(
        default_factory=list, description="審査基準一覧"
    )
    expense_rules: list[ExpenseRule] = Field(
        default_factory=list, description="経費ルール一覧"
    )
    required_documents: list[RequiredDocument] = Field(
        default_factory=list, description="必要書類一覧"
    )
    numerical_requirements: NumericalRequirements = Field(
        default_factory=NumericalRequirements, description="数値要件"
    )
    deadlines: Deadlines = Field(default_factory=Deadlines, description="各種期限")
