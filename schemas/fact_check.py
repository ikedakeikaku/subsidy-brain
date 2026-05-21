"""#12 ファクトチェッカー 入出力スキーマ

出典URL存在確認・数値の原典照合・公的統計優先参照を実施する。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Input ---


class ClaimToVerify(BaseModel):
    """検証対象の主張"""

    claim_id: str = Field(..., description="主張ID")
    claim_text: str = Field(..., description="主張テキスト")
    claimed_value: str = Field(default="", description="主張された値")
    source: dict = Field(
        default_factory=dict,
        description="出典情報（name, url, year）",
    )


class FactCheckInput(BaseModel):
    """#12 ファクトチェッカー入力"""

    claims_to_verify: list[ClaimToVerify] = Field(
        default_factory=list, description="検証対象一覧"
    )
    applicant_id: str = Field(default="", description="申請者ID")


# --- Output supporting models ---


class VerifiedSource(BaseModel):
    """検証済みの出典"""

    name: str = Field(default="", description="出典名")
    url: str = Field(default="", description="URL")
    access_date: str = Field(default="", description="アクセス日（ISO8601）")
    is_public_stats: bool = Field(default=False, description="公的統計か")


class VerificationResult(BaseModel):
    """検証結果"""

    claim_id: str = Field(..., description="主張ID")
    status: str = Field(
        ...,
        description="verified | corrected | unverifiable | outdated",
    )
    verified_value: str | None = Field(
        default=None, description="検証後の値（correctedの場合）"
    )
    verified_source: VerifiedSource = Field(
        default_factory=VerifiedSource, description="検証済み出典"
    )
    citation_text: str = Field(
        default="", description="申請書用の引用テキスト"
    )
    notes: str = Field(default="", description="備考")


class FactCheckSummary(BaseModel):
    """ファクトチェック集計"""

    total: int = Field(default=0, description="検証対象数")
    verified: int = Field(default=0, description="検証済み数")
    corrected: int = Field(default=0, description="修正数")
    unverifiable: int = Field(default=0, description="検証不能数")
    outdated: int = Field(default=0, description="データ鮮度切れ数")
    reliability_score: float = Field(
        default=0.0, description="信頼性スコア（0.0-1.0）"
    )


# --- Output ---


class FactCheckOutput(BaseModel):
    """#12 ファクトチェッカー出力"""

    verification_results: list[VerificationResult] = Field(
        default_factory=list, description="検証結果一覧"
    )
    summary: FactCheckSummary = Field(
        default_factory=FactCheckSummary, description="集計"
    )
