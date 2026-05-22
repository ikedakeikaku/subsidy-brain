"""#7 経費計算

見積書（PDF/画像/テキスト）から経費項目を読み取り、税込・税抜金額を計算する。
"""

import logging
import uuid

from agents.base import BaseAgent
from schemas.expense import (
    ExpenseCalcInput,
    ExpenseCalcOutput,
    ExpenseItem,
    ExpenseTotals,
)
from tools.claude_client import call_claude, parse_json_response
from tools.ocr_tools import ocr_document, ocr_image
from tools.pdf_tools import decode_base64_pdf

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_EXTRACT_ITEMS = """\
あなたは日本の補助金申請における見積書解析の専門家です。
与えられた見積書のテキストから、全ての経費項目を正確に抽出してJSON形式で出力してください。

## 抽出する項目（items配列）
各項目について以下を記載:
- item_name: 品目名・サービス名（見積書記載の通り）
- quantity: 数量（記載がなければ1）
- unit_price: 単価（円）。税込か税抜かは税情報から判断
- tax_included: 単価が税込かどうか（true/false）
- tax_rate: 消費税率（0.10 = 10%、0.08 = 8%、免税の場合は0）

## 税情報（tax_info）
見積書全体の税に関する情報を判定:
- tax_display: 見積書に税の表記があるか（"税込", "税抜", "内税", "外税", "不明"）
- has_subtotal: 小計の記載があるか（true/false）
- subtotal_amount: 小計金額（記載がある場合）
- has_tax_amount: 消費税額の記載があるか（true/false）
- tax_amount: 消費税額（記載がある場合）
- has_total: 合計金額の記載があるか（true/false）
- total_amount: 合計金額（記載がある場合）

## 業者情報（vendor_info）
- vendor_name: 見積書発行元の会社名・屋号（読み取れない場合は空文字）
- estimate_date: 見積日（YYYY-MM-DD形式、読み取れない場合は空文字）
- estimate_number: 見積番号（読み取れない場合は空文字）
- validity: 見積有効期限（読み取れない場合は空文字）

## 注意事項
- 金額は全て数値（カンマなし）で出力
- 「一式」は数量1として扱う
- 値引き行がある場合も item として含める（unit_price を負の値にする）
- 送料・手数料も別の item として含める

出力形式（JSONのみ）:
```json
{
  "items": [...],
  "tax_info": {...},
  "vendor_info": {...}
}
```
"""

_SYSTEM_PROMPT_CLASSIFY_CATEGORY = """\
あなたは日本の中小企業向け補助金の経費分類の専門家です。
与えられた経費項目名から、入力で指定された「補助金プログラム」に対応する
経費カテゴリ一覧の中から最も適切な分類を判定してください。

入力に経費カテゴリ一覧が含まれていない場合は、以下の代表的なカテゴリを参照してください
（小規模事業者持続化補助金の例）:

1. 機械装置等費: 機械・設備・器具の購入費
2. 広報費: チラシ・パンフレット・ポスター等の作成・配布費
3. ウェブサイト関連費: ウェブサイト・EC サイトの作成・更新費、インターネット広告費
4. 展示会等出展費: 展示会・商談会等への出展費
5. 旅費: 補助事業に必要な出張旅費
6. 開発費: 新商品・新サービスの開発に必要な原材料費等
7. 資料購入費: 補助事業に必要な図書・資料の購入費
8. 雑役務費: 補助事業に必要なアルバイト・臨時雇用の費用
9. 借料: 補助事業に必要な設備・機器のリース・レンタル費
10. 設備処分費: 補助事業のために必要な設備の処分費
11. 委託・外注費: 補助事業の一部を第三者に委託・外注する費用

各項目について、以下のJSON形式で出力してください:
```json
{
  "classifications": [
    {"item_name": "...", "category": "...", "confidence": 0.9}
  ]
}
```

confidence は判定の確信度（0.0〜1.0）です。
"""


class ExpenseCalc(BaseAgent):
    """#7 経費計算: 見積書から経費項目を抽出し、金額を計算する。"""

    agent_id = "#7"
    agent_name = "経費計算"

    async def _execute_impl(self, input_data: ExpenseCalcInput) -> ExpenseCalcOutput:
        """見積書を解析し、経費明細と合計を返す。

        処理フロー:
          1. source_type に応じてテキスト抽出
          2. Claude で経費項目抽出
          3. 税額判定ロジック
          4. 経費カテゴリ分類
          5. 合計計算
          6. ExpenseCalcOutput を組み立て
        """
        self.logger.info(
            "経費計算開始: applicant=%s, source_type=%s",
            input_data.applicant_id,
            input_data.source_type,
        )

        try:
            # 1. source_type に応じたテキスト抽出
            extracted_text = await self._extract_text(input_data)
            self.logger.info("テキスト抽出完了: %d 文字", len(extracted_text))

            # 2. Claude で経費項目抽出
            raw_data = await self._extract_items(extracted_text)
            self.logger.info(
                "経費項目抽出完了: %d 項目", len(raw_data.get("items", []))
            )

            # 3. 税額判定 + ExpenseItem 構築
            tax_info = raw_data.get("tax_info", {})
            raw_items = raw_data.get("items", [])
            vendor_info = raw_data.get("vendor_info", {})

            tax_method = self._determine_tax_method(tax_info)
            expense_items = self._build_expense_items(raw_items, tax_info, tax_method)

            # 4. 経費カテゴリ分類
            expense_items = await self._classify_categories(
                expense_items, input_data.expense_category_hint
            )

            # 5. 合計計算
            totals = self._calculate_totals(expense_items, tax_method)

            # 6. 警告生成
            warnings = self._generate_warnings(expense_items, tax_method, tax_info)

            # 7. vendor_name 決定
            vendor_name = (
                input_data.vendor_name
                or vendor_info.get("vendor_name", "")
                or ""
            )

            # 8. UUID 生成
            estimate_id = str(uuid.uuid4())

            requires_human_confirm = tax_method == "manual_required" or any(
                w for w in warnings
            )

            output = ExpenseCalcOutput(
                estimate_id=estimate_id,
                vendor_name=vendor_name,
                items=expense_items,
                totals=totals,
                warnings=warnings,
                requires_human_confirm=requires_human_confirm,
            )

            self.logger.info(
                "経費計算完了: %d項目, 税抜合計=%.0f, 税込合計=%.0f, 要確認=%s",
                len(expense_items),
                totals.total_excl_tax,
                totals.total_incl_tax,
                requires_human_confirm,
            )
            return output

        except Exception as e:
            await self.on_error(e)
            raise

    async def _extract_text(self, input_data: ExpenseCalcInput) -> str:
        """source_type に応じたテキスト抽出を行う。"""
        source_type = input_data.source_type
        raw_data = input_data.raw_data

        if source_type == "text":
            self.logger.info("テキスト入力: そのまま使用")
            return raw_data

        if source_type == "pdf":
            self.logger.info("PDF入力: pdfplumber + OCRフォールバック")
            file_data = decode_base64_pdf(raw_data)
            return await ocr_document(file_data, "application/pdf")

        if source_type == "image":
            self.logger.info("画像入力: Claude Vision OCR")
            import base64
            image_data = base64.b64decode(raw_data)
            # 画像のMIMEタイプを推定（デフォルトはJPEG）
            media_type = self._detect_image_type(image_data)
            return await ocr_image(image_data, media_type)

        raise ValueError(f"未対応の source_type: {source_type}")

    @staticmethod
    def _detect_image_type(data: bytes) -> str:
        """バイナリデータからMIMEタイプを推定する。"""
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        # デフォルトはJPEG
        return "image/jpeg"

    async def _extract_items(self, text: str) -> dict:
        """Claude で見積書から経費項目を抽出する。"""
        self.logger.info("Claude呼び出し: 経費項目抽出")
        response = await call_claude(
            system_prompt=_SYSTEM_PROMPT_EXTRACT_ITEMS,
            user_message=f"以下の見積書テキストから全ての経費項目を抽出してください。\n\n{text}",
            temperature=0.1,
            max_tokens=8192,
        )
        return parse_json_response(response)

    @staticmethod
    def _determine_tax_method(tax_info: dict) -> str:
        """税額判定方法を決定する。"""
        tax_display = tax_info.get("tax_display", "不明")

        # 明示的な税表記がある場合
        if tax_display in ("税込", "内税", "税抜", "外税"):
            return "explicit"

        # 小計と合計から推定を試みる
        has_subtotal = tax_info.get("has_subtotal", False)
        has_tax_amount = tax_info.get("has_tax_amount", False)
        has_total = tax_info.get("has_total", False)

        if has_subtotal and has_tax_amount and has_total:
            subtotal = tax_info.get("subtotal_amount", 0)
            tax_amount = tax_info.get("tax_amount", 0)
            total = tax_info.get("total_amount", 0)
            if subtotal > 0 and total > 0:
                # 小計 + 税 ≒ 合計 であれば税抜表記と推定
                if abs((subtotal + tax_amount) - total) < 10:
                    return "inferred"

        if has_total and has_subtotal:
            subtotal = tax_info.get("subtotal_amount", 0)
            total = tax_info.get("total_amount", 0)
            if subtotal > 0 and total > subtotal:
                # 合計 > 小計 なら外税と推定
                return "inferred"

        # 判定不能
        return "manual_required"

    @staticmethod
    def _build_expense_items(
        raw_items: list[dict],
        tax_info: dict,
        tax_method: str,
    ) -> list[ExpenseItem]:
        """抽出した項目から ExpenseItem リストを構築する。"""
        items: list[ExpenseItem] = []
        tax_display = tax_info.get("tax_display", "不明")

        for raw in raw_items:
            item_name = raw.get("item_name", "")
            quantity = raw.get("quantity", 1)
            unit_price = raw.get("unit_price", 0)
            tax_included = raw.get("tax_included", True)
            tax_rate = raw.get("tax_rate", 0.10)

            # 税込・税抜の判定
            if tax_method == "explicit":
                if tax_display in ("税込", "内税"):
                    tax_included = True
                elif tax_display in ("税抜", "外税"):
                    tax_included = False

            if tax_included:
                unit_price_incl = unit_price
                unit_price_excl = round(unit_price / (1 + tax_rate))
            else:
                unit_price_excl = unit_price
                unit_price_incl = round(unit_price * (1 + tax_rate))

            subtotal_excl = unit_price_excl * quantity

            items.append(
                ExpenseItem(
                    item_name=item_name,
                    quantity=quantity,
                    unit_price_incl_tax=unit_price_incl,
                    unit_price_excl_tax=unit_price_excl,
                    tax_rate=tax_rate,
                    subtotal_excl_tax=subtotal_excl,
                    expense_category="",  # 後で分類
                    is_eligible=True,
                    ineligible_reason=None,
                )
            )

        return items

    async def _classify_categories(
        self,
        items: list[ExpenseItem],
        category_hint: str | None,
    ) -> list[ExpenseItem]:
        """経費カテゴリを分類する。"""
        if not items:
            return items

        # ヒントが指定されていれば全項目に適用
        if category_hint:
            self.logger.info("経費カテゴリヒント適用: %s", category_hint)
            for item in items:
                item.expense_category = category_hint
            return items

        # Claude でカテゴリ分類
        self.logger.info("Claude呼び出し: 経費カテゴリ分類")
        item_names = [item.item_name for item in items]
        item_list_text = "\n".join(f"- {name}" for name in item_names)

        response = await call_claude(
            system_prompt=_SYSTEM_PROMPT_CLASSIFY_CATEGORY,
            user_message=f"以下の経費項目を分類してください:\n\n{item_list_text}",
            temperature=0.1,
            max_tokens=4096,
        )

        try:
            classifications = parse_json_response(response)
            category_map: dict[str, str] = {}
            for cls in classifications.get("classifications", []):
                category_map[cls["item_name"]] = cls["category"]

            for item in items:
                if item.item_name in category_map:
                    item.expense_category = category_map[item.item_name]
        except (ValueError, KeyError) as e:
            self.logger.warning("カテゴリ分類のパースに失敗: %s。未分類のまま継続。", e)

        return items

    @staticmethod
    def _calculate_totals(items: list[ExpenseItem], tax_method: str) -> ExpenseTotals:
        """経費合計を計算する。"""
        total_excl = sum(item.subtotal_excl_tax for item in items)
        total_incl = sum(item.unit_price_incl_tax * item.quantity for item in items)
        total_tax = total_incl - total_excl
        eligible_amount = sum(
            item.subtotal_excl_tax for item in items if item.is_eligible
        )
        # 補助率 2/3 で計算（デフォルト）
        subsidy_amount = round(eligible_amount * 2 / 3)

        return ExpenseTotals(
            total_incl_tax=total_incl,
            total_excl_tax=total_excl,
            total_tax=total_tax,
            eligible_amount=eligible_amount,
            subsidy_amount=subsidy_amount,
            tax_determination_method=tax_method,
        )

    @staticmethod
    def _generate_warnings(
        items: list[ExpenseItem],
        tax_method: str,
        tax_info: dict,
    ) -> list[str]:
        """警告メッセージを生成する。"""
        warnings: list[str] = []

        if tax_method == "manual_required":
            warnings.append(
                "見積書から税込・税抜の判定ができませんでした。"
                "手動で確認してください。"
            )

        # 高額項目の警告（50万円超 → 相見積もりが必要な可能性）
        for item in items:
            if item.subtotal_excl_tax > 500000:
                warnings.append(
                    f"「{item.item_name}」が50万円超（{item.subtotal_excl_tax:,.0f}円）です。"
                    f"相見積もりが必要な可能性があります。"
                )

        # カテゴリ未分類の項目
        unclassified = [item.item_name for item in items if not item.expense_category]
        if unclassified:
            warnings.append(
                f"経費カテゴリが未分類の項目があります: {', '.join(unclassified)}"
            )

        return warnings
