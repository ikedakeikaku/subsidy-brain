"""#8 ストーリービルダー 入出力スキーマ

審査基準から逆算して申請書のストーリーを構築する。
電子申請フォームの階層構造（経営計画・補助事業計画・加点）に対応。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ============================================================
# Input supporting models
# ============================================================


class PastApplication(BaseModel):
    """前回申請の情報（再申請時に参照）"""

    submission_round: str = Field(default="", description="前回の公募回次（例: 第17回）")
    result: str = Field(
        default="rejected",
        description="前回の結果: rejected（不採択）| adopted（採択済・再申請）",
    )
    rejection_reasons: list[str] = Field(
        default_factory=list,
        description="不採択理由・審査フィードバック（箇条書き）",
    )
    past_story: dict = Field(
        default_factory=dict,
        description="前回の申請書ストーリー（StoryBuilderOutput形式）",
    )
    past_application_pdf_base64: str = Field(
        default="",
        description="前回の申請書PDF（Base64エンコード）",
    )
    consultant_notes: str = Field(
        default="",
        description="コンサルによる前回の振り返りメモ（改善ポイント等）",
    )


class DataSources(BaseModel):
    """ストーリー構築に使用するデータソース"""

    hearing: dict = Field(default_factory=dict, description="ヒアリングデータ")
    financial: dict = Field(default_factory=dict, description="#6 決算書読込の出力")
    expenses: dict = Field(default_factory=dict, description="#7 経費計算の出力")
    scoring_criteria: list = Field(
        default_factory=list, description="#5 審査基準一覧"
    )
    market_data: dict | None = Field(
        default=None, description="#10 市場分析の出力（Phase 3）"
    )
    fact_check: dict | None = Field(
        default=None, description="#12 ファクトチェックの出力（Phase 3）"
    )
    past_application: PastApplication | None = Field(
        default=None, description="前回申請の情報（再申請時のみ）"
    )


class RevisionRequest(BaseModel):
    """修正依頼"""

    section: str = Field(..., description="修正対象セクションID（例: sec_1_1）")
    comment: str = Field(..., description="修正コメント")


class HumanFeedback(BaseModel):
    """人的フィードバック"""

    approved_sections: list[str] = Field(
        default_factory=list, description="承認済みセクションID一覧"
    )
    revision_requests: list[RevisionRequest] = Field(
        default_factory=list, description="修正依頼一覧"
    )


# ============================================================
# Input
# ============================================================


class StoryBuilderInput(BaseModel):
    """#8 ストーリービルダー入力"""

    action: str = Field(..., description="init | update | revalidate")
    applicant_id: str = Field(..., description="申請者ID")
    data_sources: DataSources = Field(
        default_factory=DataSources, description="データソース"
    )
    human_feedback: HumanFeedback | None = Field(
        default=None, description="人的フィードバック"
    )


# ============================================================
# Section base and variants
# ============================================================


class StorySection(BaseModel):
    """ストーリーの各セクション（汎用基底）"""

    text: str = Field(default="", description="本文")
    key_points: list[str] = Field(default_factory=list, description="要点リスト")
    data_references: list[str] = Field(
        default_factory=list, description="データ参照元（出典・数値の根拠）"
    )


class StorySectionWithTable(StorySection):
    """テーブルデータを持つセクション"""

    table_data: list[list[str]] = Field(
        default_factory=list,
        description="表データ（行×列の二次元リスト。先頭行はヘッダー）",
    )


class StorySectionWithBold(StorySection):
    """強調ワードを持つセクション"""

    bold_terms: list[str] = Field(
        default_factory=list, description="Word出力時に太字にするキーワード"
    )


class StorySectionWithTableAndBold(StorySection):
    """テーブルデータと強調ワードを両方持つセクション"""

    table_data: list[list[str]] = Field(
        default_factory=list,
        description="表データ（行×列の二次元リスト。先頭行はヘッダー）",
    )
    bold_terms: list[str] = Field(
        default_factory=list, description="Word出力時に太字にするキーワード"
    )


# ============================================================
# Expense / Effect helpers (kept from previous version)
# ============================================================


class ExpenseMapping(BaseModel):
    """経費と課題の対応付け"""

    expense_item: str = Field(..., description="経費項目")
    solves_challenge: str = Field(..., description="解決する課題")
    estimate_id: str = Field(default="", description="見積ID")


class QuantitativeEffect(BaseModel):
    """定量的効果"""

    revenue_increase: float | None = Field(default=None, description="売上増加見込み（円）")
    new_customers: int | None = Field(default=None, description="新規顧客数見込み")
    roi_period_months: int | None = Field(
        default=None, description="投資回収期間（月）"
    )


class SolutionSection(BaseModel):
    """解決策セクション（経費マッピング付き）"""

    text: str = Field(default="", description="本文")
    expense_mapping: list[ExpenseMapping] = Field(
        default_factory=list, description="経費と課題の対応付け"
    )


class EffectSection(BaseModel):
    """期待される効果セクション（定量値付き）"""

    text: str = Field(default="", description="本文")
    quantitative: QuantitativeEffect = Field(
        default_factory=QuantitativeEffect, description="定量的効果"
    )


# ============================================================
# 経営計画セクション群（様式2 / ~4,500字）
# ============================================================


class ManagementPlanSections(BaseModel):
    """経営計画書（様式2）のセクション群 合計目安 ~4,500字"""

    sec_1_1: StorySection = Field(
        default_factory=StorySection,
        description="自社の概要 (~700字): 業種・業歴・従業員数・主要商品サービス等",
    )
    sec_1_2: StorySectionWithTable = Field(
        default_factory=StorySectionWithTable,
        description=(
            "売上・利益の状況 (~500字): 直近3期の売上・利益トレンド。"
            "table_data に売上推移表を格納"
        ),
    )
    sec_1_3: StorySection = Field(
        default_factory=StorySection,
        description="経営課題 (~500字): 現状の経営上の課題・ボトルネック",
    )
    sec_2_1: StorySectionWithBold = Field(
        default_factory=StorySectionWithBold,
        description="市場の動向 (~700字): ターゲット市場の規模・成長性・外部環境変化",
    )
    sec_2_2: StorySection = Field(
        default_factory=StorySection,
        description="顧客ニーズ (~500字): 主要顧客層のニーズ・購買行動",
    )
    sec_3: StorySectionWithBold = Field(
        default_factory=StorySectionWithBold,
        description="強み・弱み (~700字): SWOT分析を踏まえた自社の強み・弱み",
    )
    sec_4_1: StorySection = Field(
        default_factory=StorySection,
        description="経営方針・目標 (~500字): 中期的な経営方針と数値目標",
    )
    sec_4_2: StorySection = Field(
        default_factory=StorySection,
        description="今後のプラン (~400字): 目標達成に向けた具体的な行動計画",
    )


# ============================================================
# 補助事業計画セクション群（様式2 / ~5,500字）
# ============================================================


class SubsidyPlanSections(BaseModel):
    """補助事業計画書（様式2）のセクション群 合計目安 ~5,500字"""

    subsidy_project_name: StorySection = Field(
        default_factory=StorySection,
        description="事業名 (30字以内): 補助事業の名称",
    )
    subsidy_2_1: StorySection = Field(
        default_factory=StorySection,
        description="事業の概要 (~400字): 補助事業の全体像をコンパクトに説明",
    )
    subsidy_2_2: StorySectionWithBold = Field(
        default_factory=StorySectionWithBold,
        description="背景・目的 (~1,000字): 補助事業を実施する背景・狙い・課題解決との接続",
    )
    subsidy_2_3: StorySectionWithTableAndBold = Field(
        default_factory=StorySectionWithTableAndBold,
        description=(
            "具体的な取組 (~1,500字): 取組内容・実施方法の詳細。"
            "table_data にスケジュール表（月×タスク）を格納"
        ),
    )
    has_efficiency: bool = Field(
        default=False,
        description="業務効率化の取組あり (True) か否か (False)。ヒアリングデータから自動判定",
    )
    subsidy_3_1: StorySection | None = Field(
        default=None,
        description=(
            "業務効率化 背景・目的 (~500字): has_efficiency=True の場合のみ使用。"
            "効率化が必要な背景・現状の非効率ポイント"
        ),
    )
    subsidy_3_2: SolutionSection | None = Field(
        default=None,
        description=(
            "業務効率化 具体的な取組 (~800字): has_efficiency=True の場合のみ使用。"
            "expense_mapping で経費と効率化課題を対応付け"
        ),
    )
    subsidy_4_1: StorySectionWithBold = Field(
        default_factory=StorySectionWithBold,
        description="効果 (~700字): 補助事業実施後に期待される定性的・定量的効果",
    )
    subsidy_4_2: StorySectionWithTable = Field(
        default_factory=StorySectionWithTable,
        description=(
            "効果の試算 (~600字): 売上増加・コスト削減等の数値根拠を説明。"
            "table_data に試算表（項目・現状・目標・増減）を格納"
        ),
    )


# ============================================================
# 加点セクション群
# ============================================================


class BonusPointSections(BaseModel):
    """加点項目のテキスト群"""

    bonus_env_change: StorySection = Field(
        default_factory=StorySection,
        description=(
            "事業環境変化加点テキスト: 物価高騰・エネルギー価格上昇等の"
            "外部環境変化への対応を記述"
        ),
    )
    bonus_local: StorySection = Field(
        default_factory=StorySection,
        description=(
            "地方創生型加点テキスト: 地域資源活用型または地域コミュニティ事業型の"
            "どちらかに対応した記述"
        ),
    )
    consultant_memo: str = Field(
        default="",
        description="コンサル向けメモ: 加点戦略・注意事項・代替案等の内部メモ（申請書には出力しない）",
    )


# ============================================================
# 統合 StorySections（新階層構造）
# ============================================================


class StorySections(BaseModel):
    """ストーリーの全セクション（電子申請フォーム階層対応版）"""

    management_plan: ManagementPlanSections = Field(
        default_factory=ManagementPlanSections,
        description="経営計画書セクション群（様式2 / ~4,500字）",
    )
    subsidy_plan: SubsidyPlanSections = Field(
        default_factory=SubsidyPlanSections,
        description="補助事業計画書セクション群（様式2 / ~5,500字）",
    )
    bonus_points: BonusPointSections = Field(
        default_factory=BonusPointSections,
        description="加点項目セクション群",
    )


# ============================================================
# Quality / consistency models
# ============================================================


class ScoringAlignment(BaseModel):
    """審査基準との整合性"""

    criteria_item: str = Field(..., description="審査基準項目")
    addressed_in: str = Field(..., description="対応セクションID（例: sec_1_1）")
    coverage: str = Field(..., description="full | partial | missing")


class ConsistencyCheck(BaseModel):
    """一貫性チェック結果"""

    is_consistent: bool = Field(default=True, description="一貫性があるか")
    issues: list[str] = Field(default_factory=list, description="不整合点一覧")


# ============================================================
# Output
# ============================================================


class StoryBuilderOutput(BaseModel):
    """#8 ストーリービルダー出力"""

    story_version: int = Field(default=1, description="ストーリーバージョン")
    status: str = Field(
        default="draft",
        description="draft | human_review | approved | needs_revision",
    )
    sections: StorySections = Field(
        default_factory=StorySections, description="ストーリー本文（全セクション）"
    )
    scoring_alignment: list[ScoringAlignment] = Field(
        default_factory=list, description="審査基準との整合性チェック結果"
    )
    consistency_check: ConsistencyCheck = Field(
        default_factory=ConsistencyCheck, description="セクション間の一貫性チェック結果"
    )
