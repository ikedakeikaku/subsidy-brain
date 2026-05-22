"""加点項目の宣言スキーマ.

補助金ごとに加点項目の中身は異なる：

  * 持続化補助金 第19回: 事業環境変化加点 / 賃金引上げ枠 / 卒業枠 /
                       創業者加点 / 再生事業者加点 / etc.
  * ものづくり補助金 第18次: 賃上げ / サイバーセキュリティ /
                       知的財産 / 財務基盤 / 成長性 / etc.
  * 省力化投資補助金 第2回: 賃上げ / 大幅賃上げ / 地域経済牽引 /
                       特定地域 / etc.

このため加点項目は **SubsidyProfile.bonus_items** として data で宣言し、
``BonusEvaluator`` が profile に従って判定 + 本文生成を行う設計とする。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BonusItemSpec(BaseModel):
    """1つの加点項目の宣言."""

    item_id: str = Field(
        description="安定識別子（snake_case、例: 'env_change', 'wage_increase'）"
    )
    display_name: str = Field(
        description="公募要領上の名称（例: '事業環境変化加点'）"
    )
    category: str = Field(
        default="",
        description="加点カテゴリ（'重点政策加点' / '政策加点' / 'その他'）",
    )
    weight_points: int = Field(
        default=0,
        description="採点表上の点数（不明なら0）",
    )
    target_chars: int = Field(
        default=500,
        description="加点本文の目標文字数",
    )
    min_chars: int = Field(
        default=350,
        description="最低文字数（これを下回ると加点対象外のリスク）",
    )
    max_chars: int = Field(
        default=600,
        description="最大文字数",
    )
    applicability_hint: str = Field(
        default="",
        description=(
            "適用条件のヒント（自然言語）。BonusEvaluator が事業者プロファイルと"
            "照合する際の手がかりとなる。例: '前年度赤字 OR 直近1年で売上減少'"
        ),
    )
    body_prompt_hint: str = Field(
        default="",
        description=(
            "本文生成時に押さえるべきポイント（自然言語）。"
            "例: '物価高騰の具体的な原価上昇率、価格転嫁困難の理由、'"
            "'転嫁不可の構造的要因（公定価格・住宅街立地等）'"
        ),
    )


class BonusItemResult(BaseModel):
    """1つの加点項目の評価結果."""

    item_id: str
    display_name: str
    applicable: bool = Field(description="この事業者が当該加点を受けられるか")
    body_text: str = Field(default="", description="生成された加点本文")
    reasoning: str = Field(default="", description="適用判定の根拠（短文）")


__all__ = ["BonusItemSpec", "BonusItemResult"]
