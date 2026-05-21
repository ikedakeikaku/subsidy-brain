"""加点判定・最低賃金計算ツール

決算データ（赤字/黒字）から加点項目を判定し、
賃金台帳・労働者名簿のOCRから最低賃金労働者を特定する。

フロー:
1. 決算書 → 赤字/黒字 判定
2. 赤字 → 重点政策加点 + 政策加点（賃金引上げ）
   黒字 → 重点政策加点（事業環境変化加点）のみ
3. 賃金台帳 + 労働者名簿 → OCR → 最低賃金労働者特定
4. Word申請書の「自社の概要」前にセクション挿入
"""

import logging
import math

from schemas.bonus_point import (
    BonusEnvChange,
    BonusPointDecision,
    MinimumWageResult,
    WageWorkerInfo,
)
from schemas.financial import PLSummary
from tools.claude_client import call_claude_json, call_claude_vision

logger = logging.getLogger(__name__)


# ============================================================
# 都道府県別最低賃金（2025年10月改定）
# ============================================================
# 毎年10月に改定されるため、申請時点での最新値を
# mcp__perplexity__perplexity_search で検証すること
# 全国加重平均: 1,121円（前年比+66円）

MINIMUM_WAGE_BY_PREFECTURE: dict[str, int] = {
    "北海道": 1075, "青森県": 1029, "岩手県": 1031, "宮城県": 1038,
    "秋田県": 1031, "山形県": 1032, "福島県": 1033, "茨城県": 1074,
    "栃木県": 1068, "群馬県": 1063, "埼玉県": 1141, "千葉県": 1140,
    "東京都": 1226, "神奈川県": 1225, "新潟県": 1050, "富山県": 1062,
    "石川県": 1054, "福井県": 1053, "山梨県": 1052, "長野県": 1061,
    "岐阜県": 1065, "静岡県": 1097, "愛知県": 1140, "三重県": 1087,
    "滋賀県": 1080, "京都府": 1122, "大阪府": 1177, "兵庫県": 1116,
    "奈良県": 1051, "和歌山県": 1045, "鳥取県": 1030, "島根県": 1033,
    "岡山県": 1047, "広島県": 1085, "山口県": 1043, "徳島県": 1046,
    "香川県": 1036, "愛媛県": 1033, "高知県": 1023, "福岡県": 1057,
    "佐賀県": 1030, "長崎県": 1031, "熊本県": 1034, "大分県": 1035,
    "宮崎県": 1023, "鹿児島県": 1026, "沖縄県": 1023,
}


def get_prefecture_minimum_wage(prefecture: str) -> int:
    """都道府県名から最低賃金を取得する。

    都道府県名は「大阪府」「東京都」「北海道」等のフル表記で指定。
    """
    wage = MINIMUM_WAGE_BY_PREFECTURE.get(prefecture)
    if wage is None:
        logger.warning("都道府県「%s」の最低賃金が見つかりません", prefecture)
        return 0
    return wage


# ============================================================
# 赤字/黒字判定 → 加点項目決定
# ============================================================

def determine_bonus_points(
    pl: PLSummary,
    prefecture: str,
    env_change_text: str = "",
    minimum_wage_result: MinimumWageResult | None = None,
) -> BonusPointDecision:
    """決算データから加点項目を判定する。

    Args:
        pl: 損益計算書サマリー（#6 決算書読込の出力）
        prefecture: 都道府県名
        env_change_text: 事業環境変化の影響テキスト（黒字時に使用）
        minimum_wage_result: 最低賃金算出結果

    Returns:
        BonusPointDecision: 加点判定結果
    """
    is_deficit = pl.net_income < 0

    if is_deficit:
        # 赤字 → 重点政策加点（赤字事業者）+ 政策加点（賃金引上げ）
        return BonusPointDecision(
            is_deficit=True,
            net_income=pl.net_income,
            priority_policy_bonus="赤字事業者（売上減少）",
            priority_policy_bonus_type="deficit",
            policy_bonus="賃金引上げ加点",
            env_change=None,
            minimum_wage=minimum_wage_result,
        )
    else:
        # 黒字 → 重点政策加点（事業環境変化加点）のみ
        env_change = BonusEnvChange(
            impact_description=env_change_text,
            evidence="",
            response_measures=[],
        ) if env_change_text else None

        return BonusPointDecision(
            is_deficit=False,
            net_income=pl.net_income,
            priority_policy_bonus="事業環境変化加点",
            priority_policy_bonus_type="env_change",
            policy_bonus=None,
            env_change=env_change,
            minimum_wage=minimum_wage_result,
        )


# ============================================================
# 賃金台帳OCR → 最低賃金労働者特定
# ============================================================

WAGE_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "workers": {
            "type": "array",
            "description": "賃金台帳に記載されている全労働者",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "労働者氏名"},
                    "wage_system": {
                        "type": "string",
                        "enum": ["hourly", "monthly", "daily"],
                        "description": "賃金体系: hourly=時給制, monthly=月給制, daily=日給制",
                    },
                    "raw_wage": {
                        "type": "number",
                        "description": "賃金台帳記載の金額（円）。時給制なら時給、月給制なら月給額",
                    },
                    "monthly_hours": {
                        "type": "number",
                        "description": "月間所定労働時間（月給制の場合。時給制の場合はnull）",
                    },
                },
                "required": ["name", "wage_system", "raw_wage"],
            },
        },
    },
    "required": ["workers"],
}


async def extract_wage_data_from_pdf(
    file_data: bytes,
    media_type: str = "application/pdf",
) -> list[dict]:
    """賃金台帳PDFからOCRで労働者の賃金データを抽出する。

    Args:
        file_data: PDFまたは画像のバイナリデータ
        media_type: "application/pdf" or "image/png" etc.

    Returns:
        労働者データのリスト
    """
    system_prompt = (
        "あなたは賃金台帳を読み取る専門家です。\n"
        "賃金台帳に記載されている全労働者の以下の情報を抽出してください：\n"
        "1. 氏名\n"
        "2. 賃金体系（時給制/月給制/日給制）\n"
        "3. 金額（時給制なら時給額、月給制なら月給額、日給制なら日給額）\n"
        "4. 月間所定労働時間（月給制の場合のみ）\n\n"
        "代表者・事業主は除外し、雇用されている労働者のみを抽出してください。"
    )

    text_prompt = "この賃金台帳から全労働者の賃金情報を抽出してください。"

    # Vision APIでOCR
    ocr_text = await call_claude_vision(
        system_prompt=system_prompt,
        image_data=file_data,
        media_type=media_type,
        text_prompt=text_prompt,
        temperature=0.0,
    )

    # 構造化JSON抽出
    result = await call_claude_json(
        system_prompt="賃金台帳のOCR結果を構造化JSONに変換してください。",
        user_message=f"以下のOCR結果から労働者の賃金情報を構造化してください：\n\n{ocr_text}",
        json_schema=WAGE_EXTRACTION_SCHEMA,
        temperature=0.0,
    )

    return result.get("workers", [])


def calculate_hourly_wage(
    wage_system: str,
    raw_wage: float,
    monthly_hours: float | None = None,
) -> int:
    """賃金体系に応じて時間換算賃金を計算する。

    Args:
        wage_system: "hourly", "monthly", "daily"
        raw_wage: 賃金台帳記載の金額
        monthly_hours: 月間所定労働時間（月給制の場合）

    Returns:
        時間換算賃金（円、1円未満切り捨て）
    """
    if wage_system == "hourly":
        return int(raw_wage)
    elif wage_system == "monthly":
        if not monthly_hours or monthly_hours <= 0:
            logger.warning("月給制だが月間所定労働時間が未設定: %s時間", monthly_hours)
            # デフォルト: 月173.8時間（週40時間×52週÷12ヶ月）
            monthly_hours = 173.8
        return math.floor(raw_wage / monthly_hours)
    elif wage_system == "daily":
        # 日給制: 日給 ÷ 1日の所定労働時間（デフォルト8時間）
        return math.floor(raw_wage / 8)
    else:
        logger.warning("不明な賃金体系: %s", wage_system)
        return int(raw_wage)


WAGE_SYSTEM_LABELS = {
    "hourly": "時給制",
    "monthly": "月給制",
    "daily": "日給制",
}


def calculate_minimum_wage_worker(
    workers_data: list[dict],
    prefecture: str,
) -> MinimumWageResult:
    """労働者リストから最低賃金労働者を特定する。

    Args:
        workers_data: extract_wage_data_from_pdf の出力
        prefecture: 都道府県名

    Returns:
        MinimumWageResult
    """
    prefecture_min_wage = get_prefecture_minimum_wage(prefecture)

    workers: list[WageWorkerInfo] = []
    for w in workers_data:
        hourly = calculate_hourly_wage(
            w["wage_system"],
            w["raw_wage"],
            w.get("monthly_hours"),
        )
        workers.append(WageWorkerInfo(
            name=w["name"],
            wage_system=w["wage_system"],
            wage_system_label=WAGE_SYSTEM_LABELS.get(w["wage_system"], w["wage_system"]),
            raw_wage=w["raw_wage"],
            monthly_hours=w.get("monthly_hours"),
            hourly_wage=hourly,
        ))

    if not workers:
        return MinimumWageResult(
            prefecture=prefecture,
            prefecture_minimum_wage=prefecture_min_wage,
        )

    # 最低賃金労働者 = 時給が最も低い労働者
    min_worker = min(workers, key=lambda w: w.hourly_wage)
    establishment_min = min_worker.hourly_wage

    return MinimumWageResult(
        workers=workers,
        minimum_wage_worker=min_worker,
        establishment_minimum_wage=establishment_min,
        prefecture=prefecture,
        prefecture_minimum_wage=prefecture_min_wage,
        wage_gap=establishment_min - prefecture_min_wage,
    )


# ============================================================
# 事業環境変化テキスト生成（Claude API）
# ============================================================

async def generate_env_change_text(
    business_description: str,
    hearing_notes: str = "",
) -> str:
    """ヒアリング内容から事業環境変化加点用テキストを生成する。

    Args:
        business_description: 事業概要
        hearing_notes: ヒアリング議事録・メモ

    Returns:
        事業環境変化の影響テキスト（申請書記載用）
    """
    system_prompt = (
        "あなたは補助金申請書の加点セクションを作成する専門家です。\n"
        "事業環境変化加点（重点政策加点）の「影響を受けている内容」を500字程度で作成してください。\n\n"
        "## 必須要素\n"
        "- ウクライナ戦争・中東情勢（イラン等）・円安などのマクロ要因を冒頭で述べる\n"
        "- その後、事業者が実際に受けている影響を事業者視点で具体的に書く\n"
        "  - 電気代・ガス代の増加（機器名・用途を明記し、金額や上昇率を示す）\n"
        "  - 仕入資材の値上がり（品目名・輸入元・円安の影響を具体的に）\n"
        "  - 設備の維持コスト増加\n"
        "- コスト増の合計額と売上高に対する割合を示す\n"
        "- 価格転嫁できない構造的理由があればそれを述べる\n"
        "- 最後に「本事業で〇〇することで△△を目指す」で締める\n\n"
        "## 文体ルール\n"
        "- 事業者が自分の言葉で書いているような平易な文体にする\n"
        "- 「〜が上がった」「〜が重くなっている」など実感のこもった表現を使う\n"
        "- 政策用語や報告書調の硬い表現は避ける\n"
        "- 地域の人口流動やデジタル集客変化は事業環境変化加点の対象外。物価高騰に集中する\n"
    )

    user_message = f"事業概要：{business_description}\n\nヒアリング内容：{hearing_notes}"

    from tools.claude_client import call_claude
    return await call_claude(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=0.3,
        max_tokens=500,
    )


# ============================================================
# Word文書への加点セクション挿入
# ============================================================

def build_bonus_section_text(decision: BonusPointDecision) -> str:
    """加点判定結果からWord申請書に挿入するテキストを生成する。

    「自社の概要」（1-1）の前に挿入する。
    """
    lines: list[str] = []

    # 重点政策加点
    lines.append("【重点政策加点】")
    if decision.priority_policy_bonus_type == "deficit":
        lines.append("赤字事業者（売上減少）")
    else:
        lines.append("2. 事業環境変化加点")
        if decision.env_change and decision.env_change.impact_description:
            lines.append("")
            lines.append("【影響を受けている内容】")
            lines.append(decision.env_change.impact_description)

    # 政策加点（赤字の場合のみ）
    if decision.policy_bonus:
        lines.append("")
        lines.append("【政策加点】")
        lines.append(f"1. {decision.policy_bonus}")

    # 最低賃金情報
    if decision.minimum_wage and decision.minimum_wage.minimum_wage_worker:
        mw = decision.minimum_wage
        worker = mw.minimum_wage_worker
        lines.append("")
        lines.append("【事業場内最低賃金算出表】")
        lines.append(
            f"労働者氏名: {worker.name} / "
            f"賃金体系: {worker.wage_system_label} / "
            f"時給: {worker.hourly_wage:,}円 / "
            f"事業場内最低賃金: {mw.establishment_minimum_wage:,}円 / "
            f"都道府県: {mw.prefecture} / "
            f"地域別最低賃金: {mw.prefecture_minimum_wage:,}円"
        )
        if mw.wage_gap > 0:
            lines.append(
                f"※事業場内最低賃金は地域別最低賃金を{mw.wage_gap:,}円上回っている"
            )

    return "\n".join(lines)


def insert_bonus_section_before_overview(doc, bonus_text: str) -> bool:
    """Word文書の「自社の概要」（1-1）の前に加点セクションを挿入する。

    Args:
        doc: python-docx Document オブジェクト
        bonus_text: 挿入するテキスト

    Returns:
        挿入に成功した場合 True
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    # 「1-1. 自社の概要」のパラグラフを検索
    target_idx = -1
    for i, para in enumerate(doc.paragraphs):
        if "自社の概要" in para.text:
            target_idx = i
            break

    if target_idx < 0:
        logger.warning("「自社の概要」パラグラフが見つかりません")
        return False

    target_para = doc.paragraphs[target_idx]

    # テキストを段落ごとに分割して挿入
    # addprevious は target の直前に挿入するため、順番に呼ぶと正しい順序になる
    paragraphs_to_insert = bonus_text.split("\n")
    paragraphs_to_insert.append("")  # 「自社の概要」との間に空行

    for text in paragraphs_to_insert:
        new_p = OxmlElement("w:p")

        if text.startswith("【") and text.endswith("】"):
            # 見出し風にボールド
            r_elem = OxmlElement("w:r")
            rPr = OxmlElement("w:rPr")
            b = OxmlElement("w:b")
            rPr.append(b)
            r_elem.append(rPr)
            t_elem = OxmlElement("w:t")
            t_elem.set(qn("xml:space"), "preserve")
            t_elem.text = text
            r_elem.append(t_elem)
            new_p.append(r_elem)
        elif text:
            r_elem = OxmlElement("w:r")
            t_elem = OxmlElement("w:t")
            t_elem.set(qn("xml:space"), "preserve")
            t_elem.text = text
            r_elem.append(t_elem)
            new_p.append(r_elem)

        target_para._element.addprevious(new_p)

    logger.info("加点セクションを「自社の概要」前に挿入しました")
    return True
