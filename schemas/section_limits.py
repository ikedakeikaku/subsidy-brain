"""セクション文字数制限の一元定義

ストーリービルダー(#8)・申請書組立(#13)・品質チェック(#14)が共通参照する。
- target_chars: AI生成時の目標文字数（ストーリービルダーが使用）
- max_chars: Wordテンプレートへの格納上限（申請書組立・品質チェックが使用）
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionLimit:
    """セクションの文字数制限定義"""

    name: str          # セクション名（日本語）
    target_chars: int  # AI生成の目標文字数
    max_chars: int     # Wordテンプレートの格納上限


# ============================================================
# 経営計画書セクション
# ============================================================
PLAN_SECTIONS: dict[str, SectionLimit] = {
    "sec_1_1": SectionLimit("自社の概要", target_chars=700, max_chars=900),
    "sec_1_2": SectionLimit("売上・利益の状況", target_chars=500, max_chars=1100),
    "sec_1_3": SectionLimit("経営課題", target_chars=500, max_chars=900),
    "sec_2_1": SectionLimit("市場の動向", target_chars=700, max_chars=1000),
    "sec_2_2": SectionLimit("顧客ニーズ", target_chars=500, max_chars=900),
    "sec_3": SectionLimit("強み・弱み", target_chars=700, max_chars=900),
    "sec_4_1": SectionLimit("経営方針・目標", target_chars=500, max_chars=800),
    "sec_4_2": SectionLimit("今後のプラン", target_chars=400, max_chars=800),
}

# ============================================================
# 補助事業計画書セクション
# ============================================================
SUBSIDY_SECTIONS: dict[str, SectionLimit] = {
    "subsidy_project_name": SectionLimit("事業名", target_chars=30, max_chars=50),
    "subsidy_2_1": SectionLimit("事業の概要", target_chars=400, max_chars=800),
    "subsidy_2_2": SectionLimit("背景・目的", target_chars=1000, max_chars=800),
    "subsidy_2_3": SectionLimit("具体的な取組", target_chars=1500, max_chars=800),
    "subsidy_3_1": SectionLimit("業務効率化 背景・目的", target_chars=500, max_chars=1000),
    "subsidy_3_2": SectionLimit("業務効率化 具体的な取組", target_chars=800, max_chars=800),
    "subsidy_4_1": SectionLimit("効果", target_chars=700, max_chars=800),
    "subsidy_4_2": SectionLimit("効果の試算", target_chars=600, max_chars=800),
}

# ============================================================
# ボーナスポイント・コンサルメモ
# ============================================================
BONUS_SECTIONS: dict[str, SectionLimit] = {
    "bonus_env_change": SectionLimit("事業環境変化", target_chars=500, max_chars=600),
    "bonus_local": SectionLimit("地域加点", target_chars=500, max_chars=600),
    "consultant_memo": SectionLimit("コンサルメモ", target_chars=300, max_chars=400),
}

# ============================================================
# 便利なアクセス用辞書
# ============================================================

# 全セクション統合（キー → SectionLimit）
ALL_SECTIONS: dict[str, SectionLimit] = {
    **PLAN_SECTIONS,
    **SUBSIDY_SECTIONS,
    **BONUS_SECTIONS,
}


def get_target_chars() -> dict[str, int]:
    """AI生成の目標文字数マップを返す（ストーリービルダー用）。"""
    return {k: v.target_chars for k, v in ALL_SECTIONS.items()}


def get_max_chars() -> dict[str, int]:
    """Wordテンプレートの格納上限マップを返す（申請書組立用）。"""
    return {k: v.max_chars for k, v in ALL_SECTIONS.items()}


def get_plan_max_chars() -> dict[str, int]:
    """経営計画書セクションの格納上限。"""
    return {k: v.max_chars for k, v in PLAN_SECTIONS.items()}


def get_subsidy_max_chars() -> dict[str, int]:
    """補助事業計画書セクションの格納上限。"""
    return {k: v.max_chars for k, v in SUBSIDY_SECTIONS.items()}


def get_bonus_max_chars() -> dict[str, int]:
    """ボーナスセクションの格納上限。"""
    return {k: v.max_chars for k, v in BONUS_SECTIONS.items()}
