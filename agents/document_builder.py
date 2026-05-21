"""#13 申請書組立エージェント

ストーリー・経費・市場分析等の全パイプライン出力を統合し、
Word申請書（経営計画書 + 経費明細）を生成する。

テンプレートプレースホルダー:
  application_form.docx:
    sec_1_1, sec_1_2, sec_1_2_table, sec_1_3, sec_2_1, sec_2_2, sec_3,
    sec_4_1, sec_4_2, subsidy_project_name, subsidy_2_1, subsidy_2_2,
    subsidy_2_3, subsidy_2_3_schedule, has_efficiency, subsidy_3_1,
    subsidy_3_2, subsidy_4_1, subsidy_4_2, subsidy_4_2_table,
    bonus_env_change, bonus_local, consultant_memo

  expense_table.docx:
    expense_detail_table, subsidy_calc_table, funding_table
"""

import logging
import math
import os
import tempfile
from datetime import datetime, timezone

from agents.base import BaseAgent
from schemas.document_build import (
    DocumentBuildInput,
    DocumentBuildOutput,
    DocumentMetadata,
    DocumentSection,
    ExpenseTableOutput,
    FundingSources,
    GeneratedDocument,
    SubsidyCalculation,
)
from schemas.section_limits import (
    get_bonus_max_chars,
    get_plan_max_chars,
    get_subsidy_max_chars,
)
from tools.docx_tools import (
    fill_section,
    fill_section_formatted,
    insert_image,
    insert_styled_table,
    load_template,
    save_document,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 文字数制限マッピング（schemas/section_limits.py の一元定義を参照）
# ---------------------------------------------------------------------------
_PLAN_SECTION_MAP: dict[str, int] = get_plan_max_chars()
_SUBSIDY_SECTION_MAP: dict[str, int] = get_subsidy_max_chars()
_BONUS_SECTION_MAP: dict[str, int] = get_bonus_max_chars()

# 補助対象経費区分（有効カテゴリ）
_VALID_EXPENSE_CATEGORIES = {
    "①機械装置等費",
    "②広報費",
    "③ウェブサイト関連費",
    "④展示会等出展費",
    "⑤旅費",
    "⑥新商品開発費",
    "⑦借料",
    "⑧委託・外注費",
}

_WEB_CATEGORY = "③ウェブサイト関連費"

# デフォルト補助率（2/3）
_DEFAULT_SUBSIDY_RATE = 2 / 3


class DocumentBuilder(BaseAgent):
    """#13 申請書組立エージェント

    #8 ストーリー、#9 経費、#10 市場分析（任意）、#11 財務効果（任意）
    の出力を統合し、Wordテンプレートに流し込んで申請書を生成する。
    """

    agent_id = "#13"
    agent_name = "申請書組立"
    skill_injection_target = True

    async def _execute_impl(self, input_data: DocumentBuildInput) -> DocumentBuildOutput:
        """Word申請書を組み立てる。"""
        self.logger.info("申請書組立を開始 (applicant_id=%s)", input_data.applicant_id)

        tmp_dir = tempfile.mkdtemp(prefix="doc_builder_")

        try:
            # 1. 経営計画書 + 補助事業計画書の生成
            plan_doc, plan_sections = self._build_plan_document(input_data, tmp_dir)

            # 2. 経費明細（様式3）の生成
            expense_table_output, subsidy_calc, funding_sources = self._build_expense_table(
                input_data, tmp_dir
            )

            # 3. Google Drive アップロード（任意）
            drive_paths = self._upload_to_drive(
                input_data.applicant_id,
                plan_doc.file_path,
                expense_table_output.file_path if expense_table_output else None,
            )
            if drive_paths.get("plan"):
                plan_doc.file_path = drive_paths["plan"]
            if drive_paths.get("expense") and expense_table_output:
                expense_table_output.file_path = drive_paths["expense"]

            # 4. メタデータ構築
            metadata = DocumentMetadata(
                total_pages=len(plan_sections),
                generated_at=datetime.now(timezone.utc).isoformat(),
                version=1,
            )

            output = DocumentBuildOutput(
                documents=[plan_doc],
                expense_table=expense_table_output,
                funding_sources=funding_sources,
                subsidy_calculation=subsidy_calc,
                metadata=metadata,
            )

            self.logger.info("申請書組立完了: ドキュメント %d 件", len(output.documents))
            return output

        except Exception:
            self.logger.exception("申請書組立中にエラーが発生しました")
            raise

    # ======================================================================
    # 経営計画書 + 補助事業計画書
    # ======================================================================

    def _build_plan_document(
        self,
        input_data: DocumentBuildInput,
        tmp_dir: str,
    ) -> tuple[GeneratedDocument, list[DocumentSection]]:
        """application_form.docx の全23プレースホルダーを埋めて申請書を生成する。"""
        doc = load_template(input_data.template_id)
        sections: list[DocumentSection] = []

        story = input_data.story
        mgmt = story.get("sections", {}).get("management_plan", {})
        subsidy = story.get("sections", {}).get("subsidy_plan", {})
        bonus = story.get("sections", {}).get("bonus_points", {})

        # ------------------------------------------------------------------
        # 経営計画書テキストセクション（sec_1_1 〜 sec_4_2）
        # ------------------------------------------------------------------
        for placeholder, char_limit in _PLAN_SECTION_MAP.items():
            section_data = mgmt.get(placeholder, {})
            text = self._extract_text(section_data)
            bold_terms = self._extract_bold_terms(section_data)

            if bold_terms:
                char_count = fill_section_formatted(
                    doc, placeholder, text, bold_terms=bold_terms, max_chars=char_limit
                )
            else:
                char_count = fill_section(doc, placeholder, text, max_chars=char_limit)

            sections.append(
                DocumentSection(
                    section_name=placeholder,
                    char_count=char_count,
                    char_limit=char_limit,
                    has_charts=False,
                )
            )

        # ------------------------------------------------------------------
        # sec_1_2_table: 売上推移テーブル
        # ------------------------------------------------------------------
        sec_1_2_data = mgmt.get("sec_1_2", {})
        table_data_1_2 = self._extract_table_data(sec_1_2_data)
        if table_data_1_2 and len(table_data_1_2) >= 2:
            headers = [str(h) for h in table_data_1_2[0]]
            rows = [[str(c) for c in row] for row in table_data_1_2[1:]]
            insert_styled_table(doc, "sec_1_2_table", headers, rows)
        else:
            fill_section(doc, "sec_1_2_table", "")

        # ------------------------------------------------------------------
        # 売上推移グラフ（sec_1_2 の table_data から生成を試みる）
        # ------------------------------------------------------------------
        revenue_chart_path = self._try_generate_revenue_chart(sec_1_2_data, tmp_dir)
        if revenue_chart_path:
            try:
                insert_image(doc, "sec_1_2_chart", revenue_chart_path)
                if sections:
                    # sec_1_2 セクションに has_charts を立てる
                    for s in sections:
                        if s.section_name == "sec_1_2":
                            s.has_charts = True
                            break
            except Exception:
                self.logger.warning("売上推移グラフの挿入に失敗しました", exc_info=True)
                fill_section(doc, "sec_1_2_chart", "")

        # ------------------------------------------------------------------
        # 補助事業計画書テキストセクション
        # ------------------------------------------------------------------
        for placeholder, char_limit in _SUBSIDY_SECTION_MAP.items():
            # has_efficiency が False の場合、subsidy_3_1 / subsidy_3_2 は空にする
            has_efficiency = subsidy.get("has_efficiency", True)
            if not has_efficiency and placeholder in ("subsidy_3_1", "subsidy_3_2"):
                fill_section(doc, placeholder, "")
                sections.append(
                    DocumentSection(
                        section_name=placeholder,
                        char_count=0,
                        char_limit=char_limit,
                        has_charts=False,
                    )
                )
                continue

            section_data = subsidy.get(placeholder, {})
            text = self._extract_text(section_data)
            bold_terms = self._extract_bold_terms(section_data)

            if bold_terms:
                char_count = fill_section_formatted(
                    doc, placeholder, text, bold_terms=bold_terms, max_chars=char_limit
                )
            else:
                char_count = fill_section(doc, placeholder, text, max_chars=char_limit)

            sections.append(
                DocumentSection(
                    section_name=placeholder,
                    char_count=char_count,
                    char_limit=char_limit,
                    has_charts=False,
                )
            )

        # ------------------------------------------------------------------
        # has_efficiency: "はい" / "いいえ"
        # ------------------------------------------------------------------
        has_efficiency_val = subsidy.get("has_efficiency", True)
        efficiency_text = "はい" if has_efficiency_val else "いいえ"
        fill_section(doc, "has_efficiency", efficiency_text)

        # ------------------------------------------------------------------
        # subsidy_2_3_schedule: スケジュールテーブル
        # ------------------------------------------------------------------
        subsidy_2_3_data = subsidy.get("subsidy_2_3", {})
        table_data_sched = self._extract_table_data(subsidy_2_3_data)
        if table_data_sched and len(table_data_sched) >= 2:
            headers = [str(h) for h in table_data_sched[0]]
            rows = [[str(c) for c in row] for row in table_data_sched[1:]]
            insert_styled_table(doc, "subsidy_2_3_schedule", headers, rows)
        else:
            fill_section(doc, "subsidy_2_3_schedule", "")

        # ------------------------------------------------------------------
        # subsidy_4_2_table: 財務効果テーブル
        # ------------------------------------------------------------------
        subsidy_4_2_data = subsidy.get("subsidy_4_2", {})
        table_data_effect = self._extract_table_data(subsidy_4_2_data)
        if table_data_effect and len(table_data_effect) >= 2:
            headers = [str(h) for h in table_data_effect[0]]
            rows = [[str(c) for c in row] for row in table_data_effect[1:]]
            insert_styled_table(doc, "subsidy_4_2_table", headers, rows)
        else:
            fill_section(doc, "subsidy_4_2_table", "")

        # ------------------------------------------------------------------
        # 財務効果グラフ（subsidy_4_2 の table_data から生成を試みる）
        # ------------------------------------------------------------------
        effect_chart_path = self._try_generate_effect_chart(subsidy_4_2_data, tmp_dir)
        if effect_chart_path:
            try:
                insert_image(doc, "subsidy_4_2_chart", effect_chart_path)
                for s in sections:
                    if s.section_name == "subsidy_4_2":
                        s.has_charts = True
                        break
            except Exception:
                self.logger.warning("財務効果グラフの挿入に失敗しました", exc_info=True)
                fill_section(doc, "subsidy_4_2_chart", "")

        # ------------------------------------------------------------------
        # ボーナスポイント・コンサルメモ
        # ------------------------------------------------------------------
        for placeholder, char_limit in _BONUS_SECTION_MAP.items():
            if placeholder == "consultant_memo":
                # consultant_memo は文字列直値の場合がある
                raw = bonus.get("consultant_memo", "")
                text = str(raw) if raw else ""
                char_count = fill_section(doc, placeholder, text, max_chars=char_limit)
            else:
                section_data = bonus.get(placeholder, {})
                text = self._extract_text(section_data)
                bold_terms = self._extract_bold_terms(section_data)
                if bold_terms:
                    char_count = fill_section_formatted(
                        doc, placeholder, text, bold_terms=bold_terms, max_chars=char_limit
                    )
                else:
                    char_count = fill_section(doc, placeholder, text, max_chars=char_limit)

            sections.append(
                DocumentSection(
                    section_name=placeholder,
                    char_count=char_count,
                    char_limit=char_limit,
                    has_charts=False,
                )
            )

        # ------------------------------------------------------------------
        # 保存
        # ------------------------------------------------------------------
        output_path = os.path.join(tmp_dir, "経営計画書.docx")
        saved_path = save_document(doc, output_path)

        plan_doc = GeneratedDocument(
            doc_type="経営計画書",
            file_path=saved_path,
            sections=sections,
        )
        return plan_doc, sections

    # ======================================================================
    # 経費明細（様式3）
    # ======================================================================

    def _build_expense_table(
        self,
        input_data: DocumentBuildInput,
        tmp_dir: str,
    ) -> tuple[ExpenseTableOutput | None, SubsidyCalculation | None, FundingSources | None]:
        """expense_table.docx の3プレースホルダーを埋めて経費明細を生成する。"""
        expenses = input_data.expenses
        if not expenses:
            self.logger.info("経費データなし。経費明細の生成をスキップします。")
            return None, None, None

        try:
            doc = load_template("expense_table")
        except FileNotFoundError:
            self.logger.warning(
                "経費明細テンプレートが見つかりません。空のドキュメントで生成します。"
            )
            from docx import Document as DocxDocument
            doc = DocxDocument()

        # 経費アイテムの正規化
        items = self._normalize_expense_items(expenses)

        # ------------------------------------------------------------------
        # expense_detail_table
        # ------------------------------------------------------------------
        detail_headers = [
            "No.",
            "経費区分",
            "内容",
            "経費内訳",
            "補助対象経費（税抜）",
            "備考",
            "購入予定先",
        ]
        detail_rows: list[list[str]] = []
        for idx, item in enumerate(items, start=1):
            category = item.get("expense_category", "")
            # カテゴリが有効でなければそのまま記載（ルールエンジン側の責務）
            detail_rows.append([
                str(idx),
                str(category),
                str(item.get("item_name", "")),
                str(item.get("expense_detail", item.get("item_name", ""))),
                f"¥{int(item.get('subtotal_excl_tax', item.get('unit_price_excl_tax', 0))):,}",
                str(item.get("note", "")),
                str(item.get("supplier", item.get("vendor", ""))),
            ])

        insert_styled_table(doc, "expense_detail_table", detail_headers, detail_rows)

        # ------------------------------------------------------------------
        # 補助金計算
        # ------------------------------------------------------------------
        subsidy_calc = self._calculate_subsidy(items)

        # subsidy_calc_table
        calc_headers = ["区分", "金額（円）", "説明"]
        calc_rows = [
            [
                "(a) ウェブサイト関連費以外の経費合計",
                f"¥{subsidy_calc.other_expense_total:,}",
                "補助対象経費のうちウェブ以外",
            ],
            [
                "(b) (a) の補助金額",
                f"¥{subsidy_calc.other_subsidy_applied:,}",
                f"(a) × {_DEFAULT_SUBSIDY_RATE:.4f}（切捨）",
            ],
            [
                "(c) ウェブサイト関連費合計",
                f"¥{subsidy_calc.web_expense_total:,}",
                "③ウェブサイト関連費の合計",
            ],
            [
                "(d) (c) の補助金額（1/4上限）",
                f"¥{subsidy_calc.web_subsidy_applied:,}",
                "(b)×1/3 または 500,000円 のいずれか低い方を上限",
            ],
            [
                "(e) 補助対象経費合計 (a)+(c)",
                f"¥{subsidy_calc.web_expense_total + subsidy_calc.other_expense_total:,}",
                "",
            ],
            [
                "(f) 補助金申請額 (b)+(d)",
                f"¥{subsidy_calc.final_subsidy_amount:,}",
                subsidy_calc.cap_note,
            ],
        ]
        insert_styled_table(doc, "subsidy_calc_table", calc_headers, calc_rows)

        # ------------------------------------------------------------------
        # funding_table（資金調達内訳）
        # ------------------------------------------------------------------
        total_expense = subsidy_calc.web_expense_total + subsidy_calc.other_expense_total
        subsidy_amount = subsidy_calc.final_subsidy_amount
        self_funding = max(0, total_expense - subsidy_amount)

        # hearing_data から借入情報を取得（任意）
        loan_amount = int(
            input_data.hearing_data.get("loan_amount", 0)
            or input_data.financial_data.get("loan_amount", 0)
            or 0
        )
        funding_note = str(input_data.hearing_data.get("funding_note", ""))

        subsidy_rate_actual = (
            subsidy_amount / total_expense if total_expense > 0 else 0.0
        )

        funding_sources = FundingSources(
            total_project_cost=total_expense,
            subsidy_amount=subsidy_amount,
            self_funding=self_funding,
            subsidy_rate=round(subsidy_rate_actual, 4),
            loan_amount=loan_amount,
            note=funding_note,
        )

        fund_headers = ["区分", "金額（円）", "割合"]
        fund_rows = [
            [
                "補助金",
                f"¥{subsidy_amount:,}",
                f"{subsidy_rate_actual * 100:.1f}%",
            ],
            [
                "自己負担",
                f"¥{self_funding:,}",
                f"{(self_funding / total_expense * 100) if total_expense > 0 else 0:.1f}%",
            ],
        ]
        if loan_amount > 0:
            fund_rows.append(
                ["  うち借入", f"¥{loan_amount:,}", ""]
            )
        fund_rows.append(
            ["合計（補助事業費）", f"¥{total_expense:,}", "100.0%"]
        )

        insert_styled_table(doc, "funding_table", fund_headers, fund_rows)

        # ------------------------------------------------------------------
        # 保存
        # ------------------------------------------------------------------
        output_path = os.path.join(tmp_dir, "経費明細.docx")
        saved_path = save_document(doc, output_path)

        expense_table_output = ExpenseTableOutput(
            file_path=saved_path,
            items=items,
        )
        return expense_table_output, subsidy_calc, funding_sources

    # ======================================================================
    # 補助金計算ロジック
    # ======================================================================

    def _calculate_subsidy(
        self,
        items: list[dict],
        subsidy_rate: float = _DEFAULT_SUBSIDY_RATE,
    ) -> SubsidyCalculation:
        """ウェブサイト関連費の1/4上限を考慮した補助金計算を行う。

        計算式:
            (a) other_total  = ウェブ以外の補助対象経費合計
            (b) other_subsidy = floor(a × rate)
            (c) web_total    = ウェブサイト関連費合計
            (d) web_subsidy  = min(floor(c × rate), min(floor(b/3), 500_000))
                               ※ (b+d)/(b+d+d) の1/4上限を解くと d <= b/3 かつ d <= 500_000
            (e) total_expense = a + c
            (f) total_subsidy = b + d
        """
        web_total = 0
        other_total = 0

        for item in items:
            amount = int(item.get("subtotal_excl_tax", item.get("unit_price_excl_tax", 0)))
            if item.get("expense_category") == _WEB_CATEGORY:
                web_total += amount
            else:
                other_total += amount

        # (b) ウェブ以外の補助金
        other_subsidy = math.floor(other_total * subsidy_rate)

        # (d) ウェブ補助金（1/4上限）
        web_subsidy_raw = math.floor(web_total * subsidy_rate)
        web_cap = min(math.floor(other_subsidy / 3), 500_000)
        web_subsidy = min(web_subsidy_raw, web_cap)
        is_web_capped = web_subsidy < web_subsidy_raw

        final_subsidy = other_subsidy + web_subsidy

        cap_note = ""
        if is_web_capped:
            cap_note = (
                f"ウェブサイト関連費の補助金は1/4上限（{web_cap:,}円）により按分されました。"
                f" 本来の補助額: ¥{web_subsidy_raw:,} → 適用後: ¥{web_subsidy:,}"
            )

        return SubsidyCalculation(
            web_expense_total=web_total,
            other_expense_total=other_total,
            gross_subsidy_before_cap=math.floor((web_total + other_total) * subsidy_rate),
            web_subsidy_cap=web_cap,
            web_subsidy_applied=web_subsidy,
            other_subsidy_applied=other_subsidy,
            final_subsidy_amount=final_subsidy,
            is_web_capped=is_web_capped,
            cap_note=cap_note,
        )

    # ======================================================================
    # グラフ生成ヘルパー
    # ======================================================================

    def _try_generate_revenue_chart(
        self, sec_1_2_data: dict, tmp_dir: str
    ) -> str | None:
        """sec_1_2 の table_data から売上推移グラフを生成する。失敗時は None を返す。"""
        table_data = self._extract_table_data(sec_1_2_data)
        if not table_data or len(table_data) < 2:
            return None

        try:
            from tools.chart_tools import generate_revenue_chart

            headers = table_data[0]
            # ヘッダー行から「年度」列と「売上」列のインデックスを推定
            year_idx = 0
            revenue_idx = 1
            for i, h in enumerate(headers):
                h_str = str(h)
                if "年度" in h_str or "年" in h_str:
                    year_idx = i
                if "売上" in h_str or "revenue" in h_str.lower():
                    revenue_idx = i

            years: list[str] = []
            revenues: list[float] = []
            for row in table_data[1:]:
                if len(row) <= max(year_idx, revenue_idx):
                    continue
                year_val = str(row[year_idx])
                rev_str = str(row[revenue_idx]).replace(",", "").replace("¥", "").replace("円", "").strip()
                try:
                    rev_val = float(rev_str)
                except ValueError:
                    continue
                years.append(year_val)
                revenues.append(rev_val)

            if not years:
                return None

            chart_path = os.path.join(tmp_dir, "revenue_chart.png")
            return generate_revenue_chart(years, revenues, chart_path)

        except Exception:
            self.logger.warning("売上推移グラフの生成に失敗しました", exc_info=True)
            return None

    def _try_generate_effect_chart(
        self, subsidy_4_2_data: dict, tmp_dir: str
    ) -> str | None:
        """subsidy_4_2 の table_data から財務効果グラフを生成する。失敗時は None を返す。"""
        table_data = self._extract_table_data(subsidy_4_2_data)
        if not table_data or len(table_data) < 2:
            return None

        try:
            from tools.chart_tools import generate_effect_chart

            headers = table_data[0]
            # 想定: ["項目", "現状", "事業後", "増加額"] 的な構造
            # 項目列=0, 現状列=1, 事業後列=2
            item_idx = 0
            current_idx = 1
            projected_idx = 2

            for i, h in enumerate(headers):
                h_str = str(h)
                if "現状" in h_str or "current" in h_str.lower():
                    current_idx = i
                if "事業後" in h_str or "projected" in h_str.lower() or "補助" in h_str:
                    projected_idx = i

            labels: list[str] = []
            current_vals: list[float] = []
            projected_vals: list[float] = []

            for row in table_data[1:]:
                if len(row) <= max(item_idx, current_idx, projected_idx):
                    continue

                def _to_float(val: str) -> float | None:
                    s = str(val).replace(",", "").replace("¥", "").replace("円", "").strip()
                    try:
                        return float(s)
                    except ValueError:
                        return None

                cur = _to_float(str(row[current_idx]))
                prj = _to_float(str(row[projected_idx]))
                if cur is None or prj is None:
                    continue

                labels.append(str(row[item_idx]))
                current_vals.append(cur)
                projected_vals.append(prj)

            if not labels:
                return None

            chart_path = os.path.join(tmp_dir, "effect_chart.png")
            return generate_effect_chart(labels, current_vals, projected_vals, chart_path)

        except Exception:
            self.logger.warning("財務効果グラフの生成に失敗しました", exc_info=True)
            return None

    # ======================================================================
    # ユーティリティ
    # ======================================================================

    def _extract_text(self, section_data: dict | str | None) -> str:
        """セクションデータから本文テキストを取り出す。"""
        if section_data is None:
            return ""
        if isinstance(section_data, str):
            return section_data
        if isinstance(section_data, dict):
            return str(section_data.get("text", ""))
        return str(section_data)

    def _extract_bold_terms(self, section_data: dict | str | None) -> list[str]:
        """セクションデータから bold_terms リストを取り出す。"""
        if not isinstance(section_data, dict):
            return []
        terms = section_data.get("bold_terms", [])
        if isinstance(terms, list):
            return [str(t) for t in terms]
        return []

    def _extract_table_data(self, section_data: dict | str | None) -> list[list] | None:
        """セクションデータから table_data を取り出す。"""
        if not isinstance(section_data, dict):
            return None
        td = section_data.get("table_data")
        if isinstance(td, list) and len(td) > 0:
            return td
        return None

    def _normalize_expense_items(self, expenses: dict) -> list[dict]:
        """経費 dict から明細アイテムリストを正規化して返す。"""
        items = expenses.get("items", [])
        if items:
            return list(items)

        # summary.by_category からフラット化
        summary = expenses.get("summary", {})
        by_category = summary.get("by_category", {})
        if by_category:
            flat: list[dict] = []
            for category, info in by_category.items():
                flat.append({
                    "expense_category": category,
                    "item_name": category,
                    "quantity": 1,
                    "unit_price_excl_tax": int(info.get("amount", 0)),
                    "subtotal_excl_tax": int(info.get("amount", 0)),
                })
            return flat

        return []

    # ======================================================================
    # Google Drive アップロード
    # ======================================================================

    def _upload_to_drive(
        self,
        applicant_id: str,
        plan_path: str,
        expense_path: str | None,
    ) -> dict[str, str]:
        """生成ドキュメントをGoogle Driveにアップロードする。

        Drive が未設定の場合はログ出力のみでスキップする。
        """
        result: dict[str, str] = {}

        try:
            from tools.drive_client import ensure_applicant_folder, upload_file

            folders = ensure_applicant_folder(applicant_id)
            submit_folder_id = folders["submit"]

            if plan_path and os.path.exists(plan_path):
                file_id = upload_file(
                    plan_path,
                    submit_folder_id,
                    mime_type=(
                        "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"
                    ),
                )
                result["plan"] = f"https://drive.google.com/file/d/{file_id}"

            if expense_path and os.path.exists(expense_path):
                file_id = upload_file(
                    expense_path,
                    submit_folder_id,
                    mime_type=(
                        "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"
                    ),
                )
                result["expense"] = f"https://drive.google.com/file/d/{file_id}"

        except Exception:
            self.logger.warning(
                "Google Driveへのアップロードをスキップしました（未設定またはエラー）",
                exc_info=True,
            )

        return result
