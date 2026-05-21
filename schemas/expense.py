"""#7 経費計算 + #9 経費ルールエンジン 入出力スキーマ

見積書から経費を読み取り計算する（#7）、
経費ルールに基づいてバリデーションする（#9）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ============================================================
# #7 経費計算
# ============================================================


class ExpenseItem(BaseModel):
    """経費明細の1行"""

    item_name: str = Field(..., description="品目名")
    quantity: int = Field(default=1, description="数量")
    unit_price_incl_tax: float = Field(default=0, description="税込単価")
    unit_price_excl_tax: float = Field(default=0, description="税抜単価")
    tax_rate: float = Field(default=0.10, description="消費税率")
    subtotal_excl_tax: float = Field(default=0, description="税抜小計")
    expense_category: str = Field(
        default="", description="経費カテゴリ（ウェブサイト関連費 等）"
    )
    is_eligible: bool = Field(default=True, description="補助対象か")
    ineligible_reason: str | None = Field(
        default=None, description="対象外の場合の理由"
    )


class ExpenseTotals(BaseModel):
    """経費合計"""

    total_incl_tax: float = Field(default=0, description="税込合計")
    total_excl_tax: float = Field(default=0, description="税抜合計")
    total_tax: float = Field(default=0, description="消費税合計")
    eligible_amount: float = Field(default=0, description="補助対象経費合計")
    subsidy_amount: float = Field(default=0, description="補助金額")
    tax_determination_method: str = Field(
        default="explicit",
        description="税額判定方法: explicit | inferred | manual_required",
    )


class ExpenseCalcInput(BaseModel):
    """#7 経費計算入力"""

    source_type: str = Field(..., description="pdf | image | text")
    raw_data: str = Field(..., description="Base64エンコードデータまたはテキスト")
    vendor_name: str | None = Field(default=None, description="仕入先・見積先名")
    applicant_id: str = Field(..., description="申請者ID")
    expense_category_hint: str | None = Field(
        default=None, description="経費カテゴリのヒント"
    )


class ExpenseCalcOutput(BaseModel):
    """#7 経費計算出力"""

    estimate_id: str = Field(..., description="見積ID（UUID）")
    vendor_name: str = Field(default="", description="仕入先名")
    items: list[ExpenseItem] = Field(default_factory=list, description="経費明細一覧")
    totals: ExpenseTotals = Field(default_factory=ExpenseTotals, description="合計")
    warnings: list[str] = Field(default_factory=list, description="警告一覧")
    requires_human_confirm: bool = Field(
        default=False, description="人的確認が必要か"
    )


# ============================================================
# #9 経費ルールエンジン
# ============================================================


class RuleCheck(BaseModel):
    """個別ルールチェック結果"""

    rule_id: str = Field(..., description="ルールID")
    rule_name: str = Field(..., description="ルール名")
    status: str = Field(..., description="pass | warning | fail")
    current_value: str = Field(default="", description="現在の値")
    limit_value: str = Field(default="", description="制限値")
    message: str = Field(default="", description="チェック結果メッセージ")
    auto_fixable: bool = Field(default=False, description="自動修正可能か")
    suggested_fix: str | None = Field(default=None, description="修正提案")


class CategorySummary(BaseModel):
    """経費カテゴリ別サマリー"""

    amount: float = Field(default=0, description="カテゴリ合計額")
    ratio: float = Field(default=0, description="全体に占める割合")


class ExpenseRuleSummary(BaseModel):
    """経費ルールチェック後のサマリー"""

    total_eligible: float = Field(default=0, description="補助対象経費合計")
    total_subsidy: float = Field(default=0, description="補助金額合計")
    final_application_amount: float = Field(default=0, description="最終申請額")
    by_category: dict[str, CategorySummary] = Field(
        default_factory=dict, description="カテゴリ別集計"
    )


class AdditionalQuote(BaseModel):
    """追加見積が必要な項目"""

    item: str = Field(..., description="品目")
    reason: str = Field(..., description="追加見積が必要な理由")


class ExpenseRuleInput(BaseModel):
    """#9 経費ルールエンジン入力"""

    expense_items: list[ExpenseItem] = Field(
        default_factory=list, description="経費明細一覧"
    )
    expense_rules: list[Any] = Field(
        default_factory=list,
        description="経費ルール一覧（guideline.ExpenseRule）",
    )
    subsidy_type: str = Field(default="通常枠", description="補助金枠種別")
    numerical_requirements: dict = Field(
        default_factory=dict, description="数値要件"
    )


class ExpenseRuleOutput(BaseModel):
    """#9 経費ルールエンジン出力"""

    validation_result: str = Field(..., description="pass | warning | fail")
    rule_checks: list[RuleCheck] = Field(
        default_factory=list, description="ルールチェック結果一覧"
    )
    summary: ExpenseRuleSummary = Field(
        default_factory=ExpenseRuleSummary, description="サマリー"
    )
    requires_additional_quotes: list[AdditionalQuote] = Field(
        default_factory=list, description="追加見積が必要な項目"
    )
