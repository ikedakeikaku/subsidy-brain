"""加点項目スキーマ

持続化補助金の加点項目（事業環境変化対応型・地方創生型）の
推薦・詳細・コンサルメモを定義する。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BonusPointRecommendation(BaseModel):
    """加点項目の推薦情報

    ヒアリングデータと公募要領を照合して、
    申請者が取得可能な加点項目を推薦する。
    """

    recommended_type: Literal["env_change", "local_resource", "local_community"] = Field(
        ...,
        description=(
            "推薦する加点種別: "
            "env_change=事業環境変化対応型, "
            "local_resource=地域資源活用型, "
            "local_community=地域コミュニティ事業型"
        ),
    )
    reason: str = Field(
        ...,
        description="この加点を推薦する根拠（ヒアリング内容・公募要領との合致点）",
    )
    text: str = Field(
        default="",
        description="申請書に記載する加点用テキスト（審査委員向けの説明文）",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="推薦の確度（0.0〜1.0）。1.0 に近いほど要件を満たしている",
    )
    missing_requirements: list[str] = Field(
        default_factory=list,
        description="加点取得のために不足している要件・追加で必要な情報",
    )


class BonusEnvChange(BaseModel):
    """事業環境変化対応型加点の詳細

    物価高騰・エネルギー価格上昇・円安等の外部環境変化への対応を
    補助事業の目的とする場合に適用される加点。
    """

    impact_description: str = Field(
        ...,
        description=(
            "事業者が受けている外部環境変化の影響の説明。"
            "仕入コスト上昇率・売上への影響額等を具体的に記述"
        ),
    )
    evidence: str = Field(
        default="",
        description=(
            "影響を裏付ける根拠・出典。"
            "例: 仕入先からの価格改定通知、業界団体の統計データ"
        ),
    )
    amount_increase: float | None = Field(
        default=None,
        description=(
            "コスト増加額（円/年）。原材料費・光熱費等の上昇分。"
            "具体的な数値がある場合に設定"
        ),
    )
    response_measures: list[str] = Field(
        default_factory=list,
        description="環境変化に対応するために補助事業で実施する具体的な措置",
    )


class BonusLocal(BaseModel):
    """地方創生型加点の詳細

    地域資源活用型または地域コミュニティ事業型のいずれかに対応。
    """

    local_type: Literal["resource", "community"] = Field(
        ...,
        description=(
            "地方創生の種別: "
            "resource=地域資源活用型（地場産品・観光資源等を活用）, "
            "community=地域コミュニティ事業型（地域住民の生活・福祉に貢献）"
        ),
    )
    contribution_description: str = Field(
        ...,
        description=(
            "地域への貢献内容の説明。"
            "地域資源活用型: 活用する地域資源の種類・調達先・活用方法。"
            "地域コミュニティ事業型: 貢献する地域課題・対象住民層・事業との関連"
        ),
    )
    local_resources_used: list[str] = Field(
        default_factory=list,
        description=(
            "活用する地域資源の一覧（地域資源活用型の場合）。"
            "例: [\"地元農産物\", \"伝統工芸素材\"]"
        ),
    )
    community_impact: str = Field(
        default="",
        description=(
            "地域コミュニティへの波及効果（地域コミュニティ事業型の場合）。"
            "雇用創出・地域課題解決への貢献等"
        ),
    )
    partner_organizations: list[str] = Field(
        default_factory=list,
        description="連携する地域団体・自治体・商工会議所等の組織名",
    )


class WageWorkerInfo(BaseModel):
    """賃金台帳から抽出した労働者情報"""

    name: str = Field(..., description="労働者氏名")
    wage_system: Literal["hourly", "monthly", "daily"] = Field(
        ..., description="賃金体系: hourly=時給制, monthly=月給制, daily=日給制"
    )
    wage_system_label: str = Field(
        default="", description="表示用ラベル: 時給制, 月給制, 日給制"
    )
    raw_wage: float = Field(..., description="賃金台帳記載の金額（円）")
    monthly_hours: float | None = Field(
        default=None, description="月間所定労働時間（月給制の場合に必要）"
    )
    hourly_wage: int = Field(..., description="時間換算賃金（円）")


class MinimumWageResult(BaseModel):
    """事業場内最低賃金算出結果"""

    workers: list[WageWorkerInfo] = Field(
        default_factory=list, description="全労働者の賃金情報"
    )
    minimum_wage_worker: WageWorkerInfo | None = Field(
        default=None, description="最低賃金労働者（時給が最も低い）"
    )
    establishment_minimum_wage: int = Field(
        default=0, description="事業場内最低賃金（円/時）"
    )
    prefecture: str = Field(default="", description="都道府県")
    prefecture_minimum_wage: int = Field(
        default=0, description="地域別最低賃金（円/時）"
    )
    wage_gap: int = Field(
        default=0,
        description="事業場内最低賃金と地域別最低賃金の差額（円）",
    )


class BonusPointDecision(BaseModel):
    """加点項目の判定結果

    決算データ（赤字/黒字）に基づき、取得可能な加点項目を判定する。
    """

    is_deficit: bool = Field(..., description="前年度決算が赤字かどうか")
    net_income: float = Field(default=0, description="当期純利益（円）")
    priority_policy_bonus: str = Field(
        ...,
        description=(
            "重点政策加点の種別。"
            "赤字の場合: 赤字事業者（売上減少）。"
            "黒字の場合: 事業環境変化加点"
        ),
    )
    priority_policy_bonus_type: Literal[
        "deficit", "env_change"
    ] = Field(
        ...,
        description="deficit=赤字事業者, env_change=事業環境変化",
    )
    policy_bonus: str | None = Field(
        default=None,
        description="政策加点（赤字の場合: 賃金引上げ加点 等。黒字の場合: None）",
    )
    env_change: BonusEnvChange | None = Field(
        default=None,
        description="事業環境変化加点の詳細（黒字の場合に生成）",
    )
    minimum_wage: MinimumWageResult | None = Field(
        default=None,
        description="最低賃金算出結果（賃金引上げ加点を申請する場合）",
    )


class ConsultantMemo(BaseModel):
    """コンサル向け加点戦略メモ

    申請書には出力されない内部向けのメモ。
    加点戦略・注意事項・代替案をまとめる。
    """

    recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "加点取得に向けた推奨アクション一覧。"
            "例: [\"売上推移表に仕入コスト列を追加する\", \"地元仕入先の請求書を収集\"]"
        ),
    )
    alternative_bonus_suggestions: list[str] = Field(
        default_factory=list,
        description=(
            "メインの加点が取得困難な場合の代替加点案。"
            "例: [\"経営力向上計画の認定を取得して加点申請する\"]"
        ),
    )
    risks: list[str] = Field(
        default_factory=list,
        description=(
            "加点申請に伴うリスク・審査上の懸念事項。"
            "例: [\"事業環境変化の影響額が小さく説得力に欠ける可能性あり\"]"
        ),
    )
    priority: str = Field(
        default="",
        description=(
            "加点取得の優先度: high | medium | low。"
            "high=確実に取れる、medium=条件次第、low=難易度が高い"
        ),
    )
    additional_documents_needed: list[str] = Field(
        default_factory=list,
        description=(
            "加点申請のために追加収集が必要な書類・資料一覧。"
            "例: [\"仕入先からの価格改定通知書\", \"市区町村の地域資源認定書\"]"
        ),
    )
