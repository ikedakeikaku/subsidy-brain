"""#14 品質チェック 入出力スキーマ

申請書の品質を審査基準と照合して検証する。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Supporting models ---


class QualityCheck(BaseModel):
    """個別品質チェック結果"""

    check_type: str = Field(
        ...,
        description="char_count | consistency | citation | expense_match | scoring_coverage | format",
    )
    target: str = Field(..., description="チェック対象（セクション名等）")
    status: str = Field(..., description="pass | warning | fail")
    message: str = Field(default="", description="チェック結果メッセージ")
    auto_fixable: bool = Field(default=False, description="自動修正可能か")
    fix_suggestion: str | None = Field(default=None, description="修正提案")


class ScoringCoverage(BaseModel):
    """審査基準カバー率"""

    total_criteria: int = Field(default=0, description="審査基準の総数")
    addressed: int = Field(default=0, description="対応済みの数")
    coverage_rate: float = Field(default=0.0, description="カバー率（0.0〜1.0）")
    missing_items: list[str] = Field(
        default_factory=list, description="未対応の審査基準項目"
    )


# --- Input / Output ---


class PastRejectionContext(BaseModel):
    """前回不採択の情報（品質チェック時に比較用）"""

    rejection_reasons: list[str] = Field(
        default_factory=list, description="前回の不採択理由"
    )
    past_story: dict = Field(
        default_factory=dict, description="前回の申請書ストーリー"
    )
    consultant_notes: str = Field(
        default="", description="コンサルの振り返りメモ"
    )


class QualityCheckInput(BaseModel):
    """#14 品質チェック入力"""

    applicant_id: str = Field(default="", description="申請者ID（スキル蓄積用）")
    documents: list[dict] = Field(
        default_factory=list, description="#13 生成ドキュメント一覧"
    )
    scoring_criteria: list = Field(
        default_factory=list, description="#5 審査基準一覧"
    )
    expense_validation: dict | None = Field(
        default=None, description="#9 経費ルールチェック出力"
    )
    fact_check: dict | None = Field(
        default=None, description="#12 ファクトチェック出力（Phase 3）"
    )
    story: dict = Field(default_factory=dict, description="#8 ストーリー出力")
    past_rejection: PastRejectionContext | None = Field(
        default=None, description="前回不採択の情報（再申請時のみ）"
    )


class QualityCheckOutput(BaseModel):
    """#14 品質チェック出力"""

    overall_score: str = Field(
        default="pass", description="pass | conditional_pass | fail"
    )
    checks: list[QualityCheck] = Field(
        default_factory=list, description="個別チェック結果一覧"
    )
    scoring_coverage: ScoringCoverage = Field(
        default_factory=ScoringCoverage, description="審査基準カバー率"
    )
    ready_for_submission: bool = Field(
        default=False, description="提出可能な状態か"
    )
    required_human_actions: list[str] = Field(
        default_factory=list, description="人的対応が必要な項目"
    )
