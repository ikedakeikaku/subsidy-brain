"""#5 要領パーサー

公募要領PDFを解析し、審査基準・経費ルール・必要書類・数値要件・期限を構造化する。
"""

import logging
import re

from agents.base import BaseAgent
from schemas.guideline import (
    Deadlines,
    ExpenseRule,
    GuidelineParserInput,
    GuidelineParserOutput,
    NumericalRequirements,
    RequiredDocument,
    ScoringCriterion,
)
from tools.claude_client import call_claude, parse_json_response
from tools.ocr_tools import ocr_pdf_page
from tools.pdf_tools import decode_base64_pdf, extract_text_from_pdf

logger = logging.getLogger(__name__)

# pdfplumber で取得したテキストがこの文字数未満の場合、OCRにフォールバック
_MIN_TEXT_PER_PAGE = 50

_SYSTEM_PROMPT_SCORING_AND_EXPENSES = """\
あなたは日本の補助金公募要領の解析専門家です。
与えられた公募要領テキストから、以下の2つの情報を正確に抽出し、JSON形式で出力してください。

## 1. 審査基準（scoring_criteria）
審査基準・採点項目を全て抽出してください。各項目について以下を記載:
- category: 「経営計画」「補助事業計画」「加点」のいずれか
- item: 審査項目名（要領の記載通り）
- max_points: 配点上限（記載がなければnull）
- description: 項目の説明・評価ポイント
- keywords: その項目で高評価を得るためのキーワード（3〜5個）

## 2. 経費ルール（expense_rules）
補助対象経費のルール・制限を全て抽出してください。各ルールについて以下を記載:
- rule_id: "ER001" のような連番ID
- category: 経費カテゴリ名（例: ウェブサイト関連費、機械装置等費、広報費 等）
- rule_type: "ratio_limit"（比率制限）| "absolute_limit"（金額上限）| "requires_quote"（相見積必要）| "excluded"（対象外）
- parameters: ルールの具体的なパラメータ（例: {"max_ratio": 0.25}、{"threshold": 500000}）
- description: ルールの説明

出力形式（JSONのみ、他のテキストは不要）:
```json
{
  "scoring_criteria": [...],
  "expense_rules": [...]
}
```
"""

_SYSTEM_PROMPT_DOCUMENTS_AND_REQUIREMENTS = """\
あなたは日本の補助金公募要領の解析専門家です。
与えられた公募要領テキストから、以下の3つの情報を正確に抽出し、JSON形式で出力してください。

## 1. 必要書類（required_documents）
申請に必要な書類を全て抽出してください。各書類について以下を記載:
- name: 書類名（例: 経営計画書、補助事業計画書、見積書 等）
- conditions: 提出条件（全員必須 / 該当者のみ 等）
- format: 書式・フォーマット指定（様式番号、ファイル形式 等）

## 2. 数値要件（numerical_requirements）
補助率・補助上限額などの数値要件を抽出してください:
- subsidy_rate: 補助率（例: 0.667 = 2/3）
- max_amount: 通常枠の補助上限額（円）
- special_max_amount: 特別枠ごとの上限額（例: {"賃金引上げ枠": 2000000, "卒業枠": 2000000}）

## 3. 期限（deadlines）
各種申請期限を抽出してください:
- application: 申請締切日（ISO8601形式: "YYYY-MM-DDTHH:MM:SS"）。日付のみの場合は "YYYY-MM-DD"
- additional: その他の期限（名称とISO8601日付の辞書）

出力形式（JSONのみ、他のテキストは不要）:
```json
{
  "required_documents": [...],
  "numerical_requirements": {...},
  "deadlines": {...}
}
```
"""


class GuidelineParser(BaseAgent):
    """#5 要領パーサー: 公募要領PDFを構造化JSONに変換する。"""

    agent_id = "#5"
    agent_name = "要領パーサー"
    skill_injection_target = True

    async def _execute_impl(self, input_data: GuidelineParserInput) -> GuidelineParserOutput:
        """公募要領PDFを解析して構造化データを返す。

        処理フロー:
          1. Base64 PDF をデコード
          2. pdfplumber でテキスト抽出（テキスト不足ページは OCR フォールバック）
          3. Claude 呼び出し1: 審査基準 + 経費ルール抽出
          4. Claude 呼び出し2: 必要書類 + 数値要件 + 期限抽出
          5. GuidelineParserOutput を組み立てて返す
        """
        self.logger.info(
            "要領パーサー開始: %s %s", input_data.subsidy_name, input_data.submission_round
        )

        try:
            # 1. Base64 PDF をデコード
            pdf_data = decode_base64_pdf(input_data.guideline_pdf)
            self.logger.info("PDFデコード完了: %d bytes", len(pdf_data))

            # 2. pdfplumber でテキスト抽出 + OCR フォールバック
            full_text = await self._extract_full_text(pdf_data)
            self.logger.info("テキスト抽出完了: %d 文字", len(full_text))

            # 3. Claude 呼び出し1: 審査基準 + 経費ルール
            scoring_and_expenses = await self._extract_scoring_and_expenses(full_text)

            # 4. Claude 呼び出し2: 必要書類 + 数値要件 + 期限
            docs_and_requirements = await self._extract_documents_and_requirements(full_text)

            # 5. subsidy_id を生成
            subsidy_id = self._generate_subsidy_id(input_data.submission_round)

            # 6. GuidelineParserOutput を組み立て
            output = GuidelineParserOutput(
                subsidy_id=subsidy_id,
                scoring_criteria=[
                    ScoringCriterion(**item)
                    for item in scoring_and_expenses.get("scoring_criteria", [])
                ],
                expense_rules=[
                    ExpenseRule(**rule)
                    for rule in scoring_and_expenses.get("expense_rules", [])
                ],
                required_documents=[
                    RequiredDocument(**doc)
                    for doc in docs_and_requirements.get("required_documents", [])
                ],
                numerical_requirements=NumericalRequirements(
                    **docs_and_requirements.get("numerical_requirements", {})
                ),
                deadlines=Deadlines(
                    **docs_and_requirements.get("deadlines", {})
                ),
            )

            self.logger.info(
                "要領パーサー完了: 審査基準 %d件, 経費ルール %d件, 必要書類 %d件",
                len(output.scoring_criteria),
                len(output.expense_rules),
                len(output.required_documents),
            )
            return output

        except Exception as e:
            await self.on_error(e)
            raise

    async def _extract_full_text(self, pdf_data: bytes) -> str:
        """PDFからテキストを抽出する。テキスト不足ページはOCRフォールバック。"""
        pages = extract_text_from_pdf(pdf_data)
        result_parts: list[str] = []

        for page_info in pages:
            page_num = page_info["page"]
            text = page_info["text"]

            if len(text.strip()) < _MIN_TEXT_PER_PAGE:
                self.logger.info(
                    "ページ %d: テキスト不足 (%d文字)。OCRフォールバック実行。",
                    page_num,
                    len(text.strip()),
                )
                text = await ocr_pdf_page(pdf_data, page_num)

            result_parts.append(f"--- ページ {page_num} ---\n{text}")

        return "\n\n".join(result_parts)

    async def _extract_scoring_and_expenses(self, full_text: str) -> dict:
        """Claude呼び出し1: 審査基準と経費ルールを抽出する。"""
        self.logger.info("Claude呼び出し1: 審査基準 + 経費ルール抽出")
        response = await call_claude(
            system_prompt=_SYSTEM_PROMPT_SCORING_AND_EXPENSES,
            user_message=f"以下の公募要領テキストから審査基準と経費ルールを抽出してください。\n\n{full_text}",
            temperature=0.1,
            max_tokens=8192,
        )
        return parse_json_response(response)

    async def _extract_documents_and_requirements(self, full_text: str) -> dict:
        """Claude呼び出し2: 必要書類・数値要件・期限を抽出する。"""
        self.logger.info("Claude呼び出し2: 必要書類 + 数値要件 + 期限抽出")
        response = await call_claude(
            system_prompt=_SYSTEM_PROMPT_DOCUMENTS_AND_REQUIREMENTS,
            user_message=f"以下の公募要領テキストから必要書類・数値要件・期限を抽出してください。\n\n{full_text}",
            temperature=0.1,
            max_tokens=8192,
        )
        return parse_json_response(response)

    @staticmethod
    def _generate_subsidy_id(submission_round: str) -> str:
        """submission_round（例: "第18回"）から subsidy_id を生成する。"""
        # 数字を抽出
        match = re.search(r"(\d+)", submission_round)
        if match:
            round_number = match.group(1)
            return f"jizokuka_{round_number}"
        # 数字がなければそのまま使う
        return f"jizokuka_{submission_round}"
