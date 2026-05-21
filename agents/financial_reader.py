"""#6 決算書読込

決算書（青色申告・freee・法人PL/BS）を読み取り、財務データを構造化する。
"""

import logging

from agents.base import BaseAgent
from schemas.financial import (
    BSSummary,
    DerivedMetrics,
    FinancialReaderInput,
    FinancialReaderOutput,
    OperatingExpenses,
    PageMap,
    PLSummary,
    RawLineItem,
)
from tools.claude_client import call_claude, parse_json_response
from tools.ocr_tools import ocr_document
from tools.pdf_tools import decode_base64_pdf, extract_text_from_pdf

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_FINANCIAL = """\
あなたは日本の中小企業・個人事業主の財務諸表を解析する専門家です。
与えられた決算書・確定申告書のテキストから、以下の財務データを正確に抽出してJSON形式で出力してください。

## 抽出する項目

### 1. 損益計算書（pl_summary）
- revenue: 売上高（円）
- cost_of_sales: 売上原価（円）
- gross_profit: 粗利益（売上高 - 売上原価）（円）
- operating_expenses: 販管費
  - total: 販管費合計（円）
  - breakdown: 費目別内訳（辞書形式。例: {"人件費": 5000000, "地代家賃": 1200000}）
- operating_income: 営業利益（円）
- net_income: 当期純利益（経常利益 or 最終利益）（円）

### 2. 貸借対照表（bs_summary）※法人の場合のみ。個人事業主の場合はnull
- total_assets: 資産合計（円）
- total_liabilities: 負債合計（円）
- net_assets: 純資産（資産合計 - 負債合計）（円）

### 3. 個別勘定科目（raw_line_items）
読み取れた全ての勘定科目を配列で出力:
- name: 勘定科目名
- amount: 金額（円）
- category: 分類（"収益" | "費用" | "資産" | "負債" | "純資産"）

### 注意事項
- 金額は全て円単位の数値（カンマなし）で出力すること
- 読み取れない項目は0を設定し、項目が存在しない場合もフィールドは必ず含めること
- 青色申告決算書の場合、売上は「売上（収入）金額」、経費の内訳は「経費の内訳」欄から読み取ること
- freee出力の場合、科目名がfreee独自の場合でも一般的な勘定科目名にマッピングすること

出力形式（JSONのみ、他のテキストは不要）:
```json
{
  "pl_summary": {
    "revenue": 0,
    "cost_of_sales": 0,
    "gross_profit": 0,
    "operating_expenses": {"total": 0, "breakdown": {}},
    "operating_income": 0,
    "net_income": 0
  },
  "bs_summary": null,
  "raw_line_items": []
}
```
"""

_SYSTEM_PROMPT_PAGE_MAP = """\
あなたは日本の財務書類のページ構成を判定する専門家です。
与えられたPDFテキスト（ページごとに区切り表示）を読み、各ページが以下のどの書類に該当するか判定してください。

- pl_pages: 損益計算書（PL）または青色申告決算書の損益部分に該当するページ番号
- bs_pages: 貸借対照表（BS）に該当するページ番号
- tax_return_pages: 確定申告書（所得税、法人税）に該当するページ番号

出力形式（JSONのみ）:
```json
{
  "pl_pages": [1, 2],
  "bs_pages": [3],
  "tax_return_pages": [4, 5]
}
```

該当するページがない場合は空配列としてください。
"""


class FinancialReader(BaseAgent):
    """#6 決算書読込: 決算書・確定申告書から財務データを構造化する。"""

    agent_id = "#6"
    agent_name = "決算書読込"

    async def _execute_impl(self, input_data: FinancialReaderInput) -> FinancialReaderOutput:
        """決算書を読み取り、構造化された財務データを返す。

        処理フロー:
          1. Base64 ファイルをデコード
          2. document_type に応じてテキスト抽出（OCR / pdfplumber）
          3. Claude で財務データ抽出
          4. 導出指標を計算
          5. ページマップを生成
          6. FinancialReaderOutput を組み立て
        """
        self.logger.info(
            "決算書読込開始: applicant=%s, type=%s, year=%s",
            input_data.applicant_id,
            input_data.document_type,
            input_data.fiscal_year,
        )

        try:
            # 1. Base64 デコード
            file_data = decode_base64_pdf(input_data.file_data)
            self.logger.info("ファイルデコード完了: %d bytes", len(file_data))

            # 2. document_type に応じたテキスト抽出
            extracted_text = await self._extract_text(file_data, input_data.document_type)
            self.logger.info("テキスト抽出完了: %d 文字", len(extracted_text))

            # 3. Claude で財務データ抽出
            financial_data = await self._extract_financial_data(
                extracted_text, input_data.document_type
            )

            # 4. PL/BS サマリー構築
            pl_raw = financial_data.get("pl_summary", {})
            pl_summary = PLSummary(
                revenue=pl_raw.get("revenue", 0),
                cost_of_sales=pl_raw.get("cost_of_sales", 0),
                gross_profit=pl_raw.get("gross_profit", 0),
                operating_expenses=OperatingExpenses(
                    total=pl_raw.get("operating_expenses", {}).get("total", 0),
                    breakdown=pl_raw.get("operating_expenses", {}).get("breakdown", {}),
                ),
                operating_income=pl_raw.get("operating_income", 0),
                net_income=pl_raw.get("net_income", 0),
            )

            bs_raw = financial_data.get("bs_summary")
            bs_summary = None
            if bs_raw and input_data.document_type in ("corporate_pl", "corporate_bs"):
                bs_summary = BSSummary(
                    total_assets=bs_raw.get("total_assets", 0),
                    total_liabilities=bs_raw.get("total_liabilities", 0),
                    net_assets=bs_raw.get("net_assets", 0),
                )

            # 5. 導出指標を計算
            derived_metrics = self._calculate_derived_metrics(pl_summary)

            # 6. 個別勘定科目
            raw_line_items = [
                RawLineItem(**item)
                for item in financial_data.get("raw_line_items", [])
            ]

            # 7. ページマップ生成
            page_map = await self._build_page_map(file_data, extracted_text)

            output = FinancialReaderOutput(
                fiscal_year=input_data.fiscal_year,
                pl_summary=pl_summary,
                bs_summary=bs_summary,
                derived_metrics=derived_metrics,
                raw_line_items=raw_line_items,
                page_map=page_map,
                source_file_path="",
            )

            self.logger.info(
                "決算書読込完了: 売上=%.0f, 営業利益=%.0f, 勘定科目=%d件",
                pl_summary.revenue,
                pl_summary.operating_income,
                len(raw_line_items),
            )
            return output

        except Exception as e:
            await self.on_error(e)
            raise

    async def _extract_text(self, file_data: bytes, document_type: str) -> str:
        """document_type に応じたテキスト抽出を行う。"""
        if document_type == "blue_return":
            # 青色申告: 手書きが多いためOCR重視
            self.logger.info("青色申告: OCRベースで抽出")
            return await ocr_document(file_data, "application/pdf")
        elif document_type == "freee_pl":
            # freee出力: 構造化されたPDF → pdfplumber 優先
            self.logger.info("freee PL: pdfplumber で抽出")
            pages = extract_text_from_pdf(file_data)
            return self._pages_to_text(pages)
        elif document_type in ("corporate_pl", "corporate_bs"):
            # 法人決算書: 標準的な会計PDF → pdfplumber 優先
            self.logger.info("法人決算書: pdfplumber で抽出")
            pages = extract_text_from_pdf(file_data)
            return self._pages_to_text(pages)
        else:
            # 不明な種別はOCRで処理
            self.logger.warning("不明なdocument_type: %s。OCRで処理。", document_type)
            return await ocr_document(file_data, "application/pdf")

    @staticmethod
    def _pages_to_text(pages: list[dict]) -> str:
        """ページリストをテキストに変換する。"""
        return "\n\n".join(
            f"--- ページ {p['page']} ---\n{p['text']}" for p in pages
        )

    async def _extract_financial_data(self, text: str, document_type: str) -> dict:
        """Claude で財務データを抽出する。"""
        self.logger.info("Claude呼び出し: 財務データ抽出 (type=%s)", document_type)

        type_description = {
            "blue_return": "青色申告決算書（個人事業主）",
            "freee_pl": "freee出力の損益計算書",
            "corporate_pl": "法人の損益計算書",
            "corporate_bs": "法人の貸借対照表",
        }.get(document_type, "財務書類")

        response = await call_claude(
            system_prompt=_SYSTEM_PROMPT_FINANCIAL,
            user_message=(
                f"以下は「{type_description}」のテキストです。"
                f"財務データを抽出してください。\n\n{text}"
            ),
            temperature=0.1,
            max_tokens=8192,
        )
        return parse_json_response(response)

    @staticmethod
    def _calculate_derived_metrics(pl: PLSummary) -> DerivedMetrics:
        """PLサマリーから導出指標を計算する。"""
        gross_margin = None
        operating_margin = None

        if pl.revenue > 0:
            gross_margin = round(pl.gross_profit / pl.revenue, 4)
            operating_margin = round(pl.operating_income / pl.revenue, 4)

        return DerivedMetrics(
            gross_margin=gross_margin,
            operating_margin=operating_margin,
            yoy_revenue_growth=None,  # 前年データがないため計算不可
        )

    async def _build_page_map(self, file_data: bytes, extracted_text: str) -> PageMap:
        """PDFのページマップを生成する。"""
        self.logger.info("Claude呼び出し: ページマップ生成")

        pages = extract_text_from_pdf(file_data)
        total_pages = len(pages)

        try:
            response = await call_claude(
                system_prompt=_SYSTEM_PROMPT_PAGE_MAP,
                user_message=(
                    f"以下のPDFテキスト（全{total_pages}ページ）の"
                    f"ページ構成を判定してください。\n\n{extracted_text}"
                ),
                temperature=0.1,
                max_tokens=1024,
            )
            page_data = parse_json_response(response)
            return PageMap(
                pl_pages=page_data.get("pl_pages", []),
                bs_pages=page_data.get("bs_pages", []),
                tax_return_pages=page_data.get("tax_return_pages", []),
                total_pages=total_pages,
            )
        except Exception as e:
            self.logger.warning("ページマップ生成に失敗: %s。デフォルト値を使用。", e)
            return PageMap(total_pages=total_pages)
