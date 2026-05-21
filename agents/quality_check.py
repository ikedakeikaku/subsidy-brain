"""#14 品質チェックエージェント

申請書の品質を審査基準と照合して検証する。
文字数・経費整合・審査基準カバー率・フォーマット等のチェックは純粋Pythonで実施し、
ストーリーの論理的一貫性チェックのみ Claude API を使用する。
"""

import logging
import re

from agents.base import BaseAgent
from schemas.quality import (
    QualityCheck,
    QualityCheckInput,
    QualityCheckOutput,
    ScoringCoverage,
)
from tools.claude_client import call_claude, parse_json_response

logger = logging.getLogger(__name__)


class QualityChecker(BaseAgent):
    """#14 品質チェックエージェント

    生成された申請書を多角的に検証し、提出可否を判定する。
    """

    agent_id = "#14"
    agent_name = "品質チェック"
    skill_injection_target = True

    async def _execute_impl(self, input_data: QualityCheckInput) -> QualityCheckOutput:
        """品質チェックを実行する。"""
        self.logger.info("品質チェックを開始")

        checks: list[QualityCheck] = []

        # 1. 文字数チェック（Pure Python）
        checks.extend(self._check_char_counts(input_data.documents))

        # 2. 経費整合チェック（Pure Python）
        checks.extend(
            self._check_expense_match(input_data.story, input_data.expense_validation)
        )

        # 3. 審査基準カバー率チェック（Pure Python）
        scoring_coverage = self._check_scoring_coverage(
            input_data.scoring_criteria, input_data.story
        )

        # 4. フォーマットチェック（Pure Python）
        checks.extend(self._check_format(input_data.documents))

        # 4.5. 「約」使用チェック（Pure Python）
        checks.extend(self._check_yaku_usage(input_data.story))

        # 4.6. 数値整合性チェック（Pure Python）
        checks.extend(self._check_numeric_consistency(input_data.story))

        # 4.7. テンプレート外セクション検出（Pure Python）
        checks.extend(self._check_unauthorized_sections(input_data.documents))

        # 5. 論理的一貫性チェック（Claude API）
        consistency = await self._check_consistency(input_data.story)
        checks.append(consistency)

        # 6. 採択パターンチェック（Pure Python）
        checks.extend(self._check_adoption_patterns(input_data.story))

        # 7. 前回不採択からの改善チェック（再申請時のみ、Claude API）
        if input_data.past_rejection and input_data.past_rejection.rejection_reasons:
            improvement_check = await self._check_past_rejection_improvements(
                input_data.story, input_data.past_rejection
            )
            checks.extend(improvement_check)

        # overall_score の判定
        has_fail = any(c.status == "fail" for c in checks)
        has_warning = any(c.status == "warning" for c in checks)

        if has_fail:
            overall_score = "fail"
        elif has_warning:
            overall_score = "conditional_pass"
        else:
            overall_score = "pass"

        # 提出可否の判定
        ready_for_submission = (
            not has_fail and scoring_coverage.coverage_rate >= 0.8
        )

        # 人的対応が必要な項目の収集
        required_human_actions = [
            c.message
            for c in checks
            if c.status in ("fail", "warning") and c.message
        ]

        output = QualityCheckOutput(
            overall_score=overall_score,
            checks=checks,
            scoring_coverage=scoring_coverage,
            ready_for_submission=ready_for_submission,
            required_human_actions=required_human_actions,
        )

        self.logger.info(
            "品質チェック完了: overall=%s, ready=%s",
            overall_score,
            ready_for_submission,
        )

        # 品質スコアに基づいてスキルを自動蓄積
        if input_data.applicant_id:
            try:
                from agents.skill_harvester import harvest_from_execution
                quality_score = scoring_coverage.coverage_rate
                harvest_from_execution(input_data.applicant_id, quality_score)
            except Exception as e:
                self.logger.warning("スキル蓄積に失敗（処理は継続）: %s", e)

        return output

    # ------------------------------------------------------------------
    # 1. 文字数チェック
    # ------------------------------------------------------------------

    def _check_char_counts(self, documents: list[dict]) -> list[QualityCheck]:
        """各ドキュメントセクションの文字数をチェックする。

        - 文字数制限の90%未満: warning（文字数不足）
        - 文字数制限の100%超過: fail（文字数超過）
        - それ以外: pass
        """
        checks: list[QualityCheck] = []

        for doc in documents:
            sections = doc.get("sections", [])
            for section in sections:
                section_name = section.get("section_name", "unknown")
                char_count = section.get("char_count", 0)
                char_limit = section.get("char_limit", 0)

                if char_limit <= 0:
                    # 制限なしの場合はスキップ
                    continue

                ratio = char_count / char_limit

                if ratio > 1.0:
                    checks.append(
                        QualityCheck(
                            check_type="char_count",
                            target=section_name,
                            status="fail",
                            message=f"文字数超過: {char_count}/{char_limit}文字 ({ratio:.0%})",
                            auto_fixable=True,
                            fix_suggestion=f"{section_name}セクションを{char_limit}文字以内に短縮してください",
                        )
                    )
                elif ratio < 0.9:
                    checks.append(
                        QualityCheck(
                            check_type="char_count",
                            target=section_name,
                            status="warning",
                            message=f"文字数不足: {char_count}/{char_limit}文字 ({ratio:.0%})",
                            auto_fixable=True,
                            fix_suggestion=f"{section_name}セクションの内容を充実させてください（目安: {char_limit}文字）",
                        )
                    )
                else:
                    checks.append(
                        QualityCheck(
                            check_type="char_count",
                            target=section_name,
                            status="pass",
                            message=f"文字数OK: {char_count}/{char_limit}文字 ({ratio:.0%})",
                        )
                    )

        return checks

    # ------------------------------------------------------------------
    # 2. 経費整合チェック
    # ------------------------------------------------------------------

    def _check_expense_match(
        self,
        story: dict,
        expense_validation: dict | None,
    ) -> list[QualityCheck]:
        """ストーリー内の経費参照と経費ルールチェック結果の整合性を検証する。"""
        checks: list[QualityCheck] = []

        # ストーリーの solution.expense_mapping から参照されている経費項目を取得
        sections = story.get("sections", {})
        solution = sections.get("solution", {})
        expense_mapping = solution.get("expense_mapping", [])

        story_expense_items = {
            m.get("expense_item", "") for m in expense_mapping if m.get("expense_item")
        }

        if not expense_validation:
            if story_expense_items:
                checks.append(
                    QualityCheck(
                        check_type="expense_match",
                        target="経費バリデーション",
                        status="warning",
                        message="経費ルールチェック結果が提供されていません。経費の整合性を確認できません。",
                    )
                )
            return checks

        # 経費ルールチェック結果から経費項目を取得
        rule_checks = expense_validation.get("rule_checks", [])
        failed_rules = [
            rc for rc in rule_checks if rc.get("status") == "fail"
        ]

        if failed_rules:
            for rule in failed_rules:
                checks.append(
                    QualityCheck(
                        check_type="expense_match",
                        target=rule.get("rule_name", "unknown"),
                        status="fail",
                        message=f"経費ルール違反: {rule.get('message', '')}",
                        auto_fixable=rule.get("auto_fixable", False),
                        fix_suggestion=rule.get("suggested_fix"),
                    )
                )

        # 経費バリデーション全体の結果
        validation_result = expense_validation.get("validation_result", "pass")
        if validation_result == "pass":
            checks.append(
                QualityCheck(
                    check_type="expense_match",
                    target="経費ルール全体",
                    status="pass",
                    message="経費ルールチェック: すべてのルールに適合しています",
                )
            )
        elif validation_result == "warning":
            checks.append(
                QualityCheck(
                    check_type="expense_match",
                    target="経費ルール全体",
                    status="warning",
                    message="経費ルールチェック: 一部警告があります",
                )
            )

        return checks

    # ------------------------------------------------------------------
    # 3. 審査基準カバー率チェック
    # ------------------------------------------------------------------

    def _check_scoring_coverage(
        self,
        scoring_criteria: list,
        story: dict,
    ) -> ScoringCoverage:
        """審査基準のカバー率を算出する。"""
        if not scoring_criteria:
            return ScoringCoverage(
                total_criteria=0,
                addressed=0,
                coverage_rate=1.0,
                missing_items=[],
            )

        # ストーリーの scoring_alignment から対応状況を取得
        scoring_alignment = story.get("scoring_alignment", [])

        # alignment マップを構築: criteria_item → coverage
        alignment_map: dict[str, str] = {}
        for alignment in scoring_alignment:
            item = alignment.get("criteria_item", "")
            coverage = alignment.get("coverage", "missing")
            if item:
                alignment_map[item] = coverage

        total = len(scoring_criteria)
        addressed = 0
        missing_items: list[str] = []

        for criterion in scoring_criteria:
            # ScoringCriterion は dict または Pydantic model
            if isinstance(criterion, dict):
                item_name = criterion.get("item", "")
            else:
                item_name = getattr(criterion, "item", "")

            coverage = alignment_map.get(item_name, "missing")
            if coverage in ("full", "partial"):
                addressed += 1
            else:
                missing_items.append(item_name)

        coverage_rate = addressed / total if total > 0 else 0.0

        return ScoringCoverage(
            total_criteria=total,
            addressed=addressed,
            coverage_rate=round(coverage_rate, 3),
            missing_items=missing_items,
        )

    # ------------------------------------------------------------------
    # 4. フォーマットチェック
    # ------------------------------------------------------------------

    def _check_format(self, documents: list[dict]) -> list[QualityCheck]:
        """ドキュメントの存在・内容有無をチェックする。"""
        checks: list[QualityCheck] = []

        if not documents:
            checks.append(
                QualityCheck(
                    check_type="format",
                    target="ドキュメント一覧",
                    status="fail",
                    message="生成されたドキュメントが存在しません",
                )
            )
            return checks

        for doc in documents:
            doc_type = doc.get("doc_type", "unknown")
            file_path = doc.get("file_path", "")
            sections = doc.get("sections", [])

            # ファイルパスのチェック
            if not file_path:
                checks.append(
                    QualityCheck(
                        check_type="format",
                        target=doc_type,
                        status="fail",
                        message=f"{doc_type}: ファイルパスが設定されていません",
                    )
                )
                continue

            # セクションが存在するか
            if not sections:
                checks.append(
                    QualityCheck(
                        check_type="format",
                        target=doc_type,
                        status="warning",
                        message=f"{doc_type}: セクション情報がありません",
                    )
                )
            else:
                # すべてのセクションにコンテンツがあるか
                empty_sections = [
                    s.get("section_name", "unknown")
                    for s in sections
                    if s.get("char_count", 0) == 0
                ]
                if empty_sections:
                    checks.append(
                        QualityCheck(
                            check_type="format",
                            target=doc_type,
                            status="warning",
                            message=f"{doc_type}: 空のセクションがあります: {', '.join(empty_sections)}",
                            auto_fixable=True,
                            fix_suggestion="空のセクションに内容を追加してください",
                        )
                    )
                else:
                    checks.append(
                        QualityCheck(
                            check_type="format",
                            target=doc_type,
                            status="pass",
                            message=f"{doc_type}: フォーマットOK",
                        )
                    )

        return checks

    # ------------------------------------------------------------------
    # 4.5. 「約」使用チェック（Pure Python）
    # ------------------------------------------------------------------

    def _check_yaku_usage(self, story: dict) -> list[QualityCheck]:
        """申請書テキスト内の「約」使用を検出する。「約」は使用禁止。"""
        checks: list[QualityCheck] = []
        sections = story.get("sections", {})
        all_text = self._extract_all_text(sections)

        # 「約」+ 数値のパターンを検出
        yaku_pattern = r"約[\d,，０-９]+[万億千百件人名組円%％割]"
        matches = re.findall(yaku_pattern, all_text)

        if matches:
            examples = matches[:5]
            checks.append(
                QualityCheck(
                    check_type="format",
                    target="「約」使用禁止",
                    status="fail",
                    message=f"「約」+数値が{len(matches)}箇所あります: {', '.join(examples)}。概算でも端数を丸めた具体値にすること",
                    auto_fixable=True,
                    fix_suggestion="「約7割」→「72%」、「約130万」→「132万」のように具体値に置換してください",
                )
            )
        else:
            checks.append(
                QualityCheck(
                    check_type="format",
                    target="「約」使用禁止",
                    status="pass",
                    message="「約」+数値の使用なし: OK",
                )
            )

        return checks

    # ------------------------------------------------------------------
    # 4.6. 数値整合性チェック（Pure Python）
    # ------------------------------------------------------------------

    def _check_numeric_consistency(self, story: dict) -> list[QualityCheck]:
        """ストーリー内の数値整合性をチェックする。"""
        checks: list[QualityCheck] = []
        sections = story.get("sections", {})
        all_text = self._extract_all_text(sections)

        # 年間売上と月間売上の整合チェック
        annual_pattern = r"年間?売上[はが：:]*[\s]*([0-9,，]+)\s*万"
        monthly_pattern = r"月間?売上[はが：:]*[\s]*([0-9,，]+)\s*万"

        annual_matches = re.findall(annual_pattern, all_text)
        monthly_matches = re.findall(monthly_pattern, all_text)

        if annual_matches and monthly_matches:
            try:
                annual = int(annual_matches[0].replace(",", "").replace("，", ""))
                monthly = int(monthly_matches[0].replace(",", "").replace("，", ""))
                expected_monthly = annual / 12
                tolerance = expected_monthly * 0.15  # 15%の許容範囲

                if abs(monthly - expected_monthly) > tolerance:
                    checks.append(
                        QualityCheck(
                            check_type="numeric_consistency",
                            target="年間売上÷12=月間売上",
                            status="warning",
                            message=f"年間売上{annual}万÷12≒{expected_monthly:.0f}万 だが月間売上は{monthly}万と記載。整合性を確認してください",
                            auto_fixable=False,
                            fix_suggestion=f"年間売上{annual}万の場合、月間売上は{expected_monthly:.0f}万前後が妥当",
                        )
                    )
            except (ValueError, ZeroDivisionError):
                pass

        # %と金額の整合チェック: 「売上のXX%（月YY万円）」
        pct_amount_pattern = r"売上の(\d+)[%％].*?[（(]月(\d+)万"
        pct_matches = re.findall(pct_amount_pattern, all_text)

        if pct_matches and monthly_matches:
            try:
                monthly_base = int(monthly_matches[0].replace(",", "").replace("，", ""))
                for pct_str, amount_str in pct_matches:
                    pct = int(pct_str)
                    amount = int(amount_str)
                    expected = monthly_base * pct / 100
                    tolerance = expected * 0.15

                    if abs(amount - expected) > tolerance:
                        checks.append(
                            QualityCheck(
                                check_type="numeric_consistency",
                                target="%×売上=金額",
                                status="warning",
                                message=f"月間売上{monthly_base}万×{pct}%≒{expected:.0f}万 だが{amount}万と記載。整合性を確認",
                                auto_fixable=False,
                                fix_suggestion=f"{pct}%×{monthly_base}万={expected:.0f}万に修正してください",
                            )
                        )
            except (ValueError, ZeroDivisionError):
                pass

        # 保守係数の検出
        hoshu_pattern = r"保守係数|安全率[×✕]|×\s*0\.[5-9]\d*\s*[（(]保守"
        if re.search(hoshu_pattern, all_text):
            checks.append(
                QualityCheck(
                    check_type="numeric_consistency",
                    target="保守係数",
                    status="fail",
                    message="保守係数が使用されています。売上試算は「顧客数×比率×単価＝売上」のストレートな計算にすること",
                    auto_fixable=True,
                    fix_suggestion="保守係数を削除し、シンプルな掛け算のみで試算してください",
                )
            )

        if not checks:
            checks.append(
                QualityCheck(
                    check_type="numeric_consistency",
                    target="数値整合性",
                    status="pass",
                    message="数値整合性チェック: 検出された不整合なし",
                )
            )

        return checks

    # ------------------------------------------------------------------
    # 4.7. テンプレート外セクション検出（Pure Python）
    # ------------------------------------------------------------------

    # 許可されたセクション番号の一覧
    ALLOWED_SECTION_NUMBERS = {
        # 経営計画
        "1-1", "1-2", "1-3", "2-1", "2-2", "3", "4-1", "4-2",
        # 補助事業計画
        "1", "2-1", "2-2", "2-3", "3-1", "3-2", "4-1", "4-2",
    }

    def _check_unauthorized_sections(self, documents: list[dict]) -> list[QualityCheck]:
        """テンプレートにないセクション番号を検出する。"""
        checks: list[QualityCheck] = []

        for doc in documents:
            sections = doc.get("sections", [])
            for section in sections:
                section_name = section.get("section_name", "")
                # セクション名から番号部分を抽出（例: "1-1. 自社の概要" → "1-1"）
                section_num_match = re.match(r"^(\d+(?:-\d+)?)\.", section_name)
                if section_num_match:
                    section_num = section_num_match.group(1)
                    if section_num not in self.ALLOWED_SECTION_NUMBERS:
                        checks.append(
                            QualityCheck(
                                check_type="format",
                                target=section_name,
                                status="fail",
                                message=f"テンプレートにないセクション「{section_name}」が検出されました。独自セクションは追加禁止",
                                auto_fixable=False,
                                fix_suggestion=f"セクション「{section_name}」を削除し、必要な内容は既存セクション（4-1等）に追記してください",
                            )
                        )

                # 全角数字のセクション番号を検出（例: "１．" "２．"）
                if re.match(r"^[１-９][０-９]*[．.]", section_name):
                    checks.append(
                        QualityCheck(
                            check_type="format",
                            target=section_name,
                            status="fail",
                            message=f"全角数字のセクション番号「{section_name}」は不可。半角の「1-1.」形式を使用すること",
                            auto_fixable=True,
                            fix_suggestion="セクション番号を半角数字+ハイフン形式（1-1., 2-1.等）に変更してください",
                        )
                    )

        return checks

    # ------------------------------------------------------------------
    # 5. 採択パターンチェック（Pure Python）
    # ------------------------------------------------------------------

    # 日本標準産業分類 大分類
    INDUSTRY_CLASSIFICATION = {
        "農業": ["農業", "農園", "農家", "栽培", "畜産"],
        "林業": ["林業", "木材"],
        "漁業": ["漁業", "水産", "養殖"],
        "鉱業": ["鉱業", "採石"],
        "建設業": ["建設", "工務店", "リフォーム", "外構", "造園", "塗装", "電気工事", "設備工事"],
        "製造業": ["製造", "工場", "加工", "メーカー"],
        "電気・ガス・熱供給・水道業": ["電力", "ガス", "水道"],
        "情報通信業": ["IT", "ソフトウェア", "システム開発", "Web制作", "アプリ"],
        "運輸業": ["運送", "運輸", "物流", "配送", "タクシー"],
        "卸売業": ["卸売", "卸", "問屋", "商社"],
        "小売業": ["小売", "販売", "ショップ", "店舗", "通販", "EC"],
        "金融業": ["金融", "保険", "証券", "銀行"],
        "不動産業": ["不動産", "賃貸", "仲介", "物件"],
        "飲食サービス業": ["飲食", "レストラン", "カフェ", "居酒屋", "食堂", "弁当", "ケータリング"],
        "宿泊業": ["ホテル", "旅館", "民宿", "宿泊"],
        "医療・福祉": ["医療", "病院", "クリニック", "歯科", "薬局", "介護", "福祉",
                     "整骨院", "鍼灸", "接骨", "整体", "マッサージ"],
        "教育・学習支援業": ["教育", "学習塾", "スクール", "教室", "学校"],
        "サービス業": ["美容", "理容", "エステ", "ネイル", "サロン",
                     "コンサルティング", "コンサル", "デザイン", "写真", "広告",
                     "清掃", "ビルメンテナンス", "修理"],
        "娯楽業": ["娯楽", "アミューズメント", "ビリヤード", "ボウリング",
                  "カラオケ", "ゲーム", "スポーツジム", "フィットネス"],
    }

    def _check_adoption_patterns(self, story: dict) -> list[QualityCheck]:
        """採択パターン分析に基づく品質チェックを実施する。

        注入スキル（ADOPTION_PATTERNS）があればそのキーワードリストを使用し、
        なければデフォルトのハードコード値にフォールバックする。

        チェック項目:
        - 自社データの有無 (-15点)
        - 課題↔施策対応 (-10点)
        - ローカルデータ (-10点)
        - 効果試算根拠 (-10点)
        - 差別化明示 (-5点)
        - 業種分類整合
        """
        checks: list[QualityCheck] = []
        sections = story.get("sections", {})

        # 全セクションのテキストを結合
        all_text = self._extract_all_text(sections)

        # 注入スキルから採択パターンの追加キーワードを取得
        extra_self_data_keywords = self._get_skill_keywords("self_data_presence")
        extra_diff_patterns = self._get_skill_keywords("differentiation")

        # 5-1. 自社データチェック
        checks.append(self._check_self_data(all_text, extra_keywords=extra_self_data_keywords))

        # 5-2. 課題↔施策対応チェック
        checks.append(self._check_challenge_solution_mapping(all_text))

        # 5-3. ローカルデータチェック
        checks.append(self._check_local_data(all_text))

        # 5-4. 効果試算根拠チェック
        checks.append(self._check_effect_calculation(all_text))

        # 5-5. 差別化明示チェック
        checks.append(self._check_differentiation(all_text, extra_patterns=extra_diff_patterns))

        # 5-6. 業種分類整合チェック
        industry_check = self._check_industry_classification(all_text, story)
        if industry_check:
            checks.append(industry_check)

        return checks

    def _get_skill_keywords(self, factor_name: str) -> list[str]:
        """注入スキルから指定要因のキーワードリストを取得する。"""
        skill_context = self.get_skill_context()
        if not skill_context:
            return []

        # スキルコンテキストからadoption_patternsを探す
        try:
            from tools.skill_store import skill_store
            skills = skill_store.search_skills(
                agent_id=self.agent_id,
                min_score=0.5,
            )
            for skill in skills:
                content = skill.content
                if content.get("name") == "adoption_patterns":
                    factors = content.get("factors", {})
                    factor = factors.get(factor_name, {})
                    # examplesからキーワードを抽出
                    examples = factor.get("examples", [])
                    if isinstance(examples, list):
                        return [str(e) for e in examples]
        except Exception:
            pass
        return []

    def _extract_all_text(self, sections: dict) -> str:
        """セクション辞書から全テキストを抽出して結合する。"""
        texts = []
        if isinstance(sections, dict):
            for key, value in sections.items():
                if isinstance(value, dict):
                    text = value.get("text", "")
                    if text:
                        texts.append(text)
                    # ネスト対応（management_plan / subsidy_plan等）
                    for sub_key, sub_value in value.items():
                        if isinstance(sub_value, dict):
                            sub_text = sub_value.get("text", "")
                            if sub_text:
                                texts.append(sub_text)
                elif isinstance(value, str) and value:
                    texts.append(value)
        return "\n".join(texts)

    def _check_self_data(self, all_text: str, extra_keywords: list[str] | None = None) -> QualityCheck:
        """自社データチェック: 自社アンケート・来店データ・顧客属性データの有無を確認。"""
        self_data_keywords = [
            "アンケート", "来店データ", "来店数", "来客数", "顧客データ",
            "顧客属性", "リピート率", "リピーター", "顧客分析",
            "売上構成比", "売上内訳", "サービス別売上", "商品別売上",
            "SNS分析", "閲覧率", "フォロワー", "インサイト",
            "カウンセリング", "ヒアリング結果", "顧客の声",
            "顧問先", "取引実績", "成約率", "受注実績",
            "単価比較", "データベース",
        ]

        # 注入スキルからの追加キーワードをマージ
        if extra_keywords:
            self_data_keywords.extend(kw for kw in extra_keywords if kw not in self_data_keywords)

        found = [kw for kw in self_data_keywords if kw in all_text]

        if found:
            return QualityCheck(
                check_type="adoption_pattern",
                target="自社データ",
                status="pass",
                message=f"自社データ確認OK: {', '.join(found[:3])}等を含む",
            )
        else:
            return QualityCheck(
                check_type="adoption_pattern",
                target="自社データ",
                status="warning",
                message="自社独自データ（アンケート・来店データ・顧客属性等）が見つかりません（-15点相当）",
                auto_fixable=False,
                fix_suggestion="自社アンケート結果、来店データ、サービス別売上構成比、SNS分析データ等を追加してください",
            )

    def _check_challenge_solution_mapping(self, all_text: str) -> QualityCheck:
        """課題↔施策対応チェック: 課題の数と施策の対応関係が明示されているか確認。"""
        # 課題パターンを検出
        challenge_patterns = [
            r"■課題\d",
            r"課題[①②③④⑤1-5１-５]",
            r"【課題\d】",
        ]
        challenge_count = 0
        for pattern in challenge_patterns:
            matches = re.findall(pattern, all_text)
            challenge_count = max(challenge_count, len(matches))

        # 施策→課題の対応表現を検出
        mapping_patterns = [
            r"→\s*施策",
            r"対応.{0,5}施策",
            r"解決する.{0,10}(取組|施策)",
            r"課題.{0,5}に対し",
            r"課題.{0,5}→",
        ]
        mapping_found = any(
            re.search(pattern, all_text) for pattern in mapping_patterns
        )

        if challenge_count >= 3 and mapping_found:
            return QualityCheck(
                check_type="adoption_pattern",
                target="課題↔施策対応",
                status="pass",
                message=f"課題↔施策対応OK: {challenge_count}個の課題と施策の対応あり",
            )
        elif challenge_count >= 3:
            return QualityCheck(
                check_type="adoption_pattern",
                target="課題↔施策対応",
                status="warning",
                message=f"課題は{challenge_count}個あるが、施策との対応関係が不明確（-10点相当）",
                auto_fixable=False,
                fix_suggestion="各施策がどの課題を解決するかを「■課題1: 〇〇 → 施策: △△」の形式で明示してください",
            )
        else:
            return QualityCheck(
                check_type="adoption_pattern",
                target="課題↔施策対応",
                status="warning",
                message=f"経営課題の分解が不足（検出: {challenge_count}個、推奨: 3〜5個）（-10点相当）",
                auto_fixable=False,
                fix_suggestion="経営課題を3〜5個に分解し、各施策との対応関係を明示してください",
            )

    def _check_local_data(self, all_text: str) -> QualityCheck:
        """ローカルデータチェック: 市区町村名＋人口データの有無を確認。"""
        # 市区町村名の検出
        local_pattern = r"[^\s]{2,6}(市|区|町|村)(の|は|に|で|、)"
        local_matches = re.findall(local_pattern, all_text)

        # 人口データの検出
        population_patterns = [
            r"人口[はが]?\s*[\d,，]+",
            r"[\d,，]+人",
            r"世帯数",
            r"年齢構成",
            r"老年人口",
            r"生産年齢人口",
            r"若年層",
        ]
        population_found = any(
            re.search(pattern, all_text) for pattern in population_patterns
        )

        has_local = len(local_matches) > 0
        has_population = population_found

        if has_local and has_population:
            return QualityCheck(
                check_type="adoption_pattern",
                target="ローカルデータ",
                status="pass",
                message="ローカルデータOK: 市区町村名と人口データを含む",
            )
        elif has_local:
            return QualityCheck(
                check_type="adoption_pattern",
                target="ローカルデータ",
                status="warning",
                message="市区町村名はあるが人口・世帯数データが不足（-10点相当）",
                auto_fixable=False,
                fix_suggestion="市区町村レベルの人口・世帯数・年齢構成データを追加してください",
            )
        else:
            return QualityCheck(
                check_type="adoption_pattern",
                target="ローカルデータ",
                status="warning",
                message="市区町村レベルのローカルデータが見つかりません。全国統計のみの可能性（-10点相当）",
                auto_fixable=False,
                fix_suggestion="市区町村名＋人口データを含むローカル市場分析を追加してください",
            )

    def _check_effect_calculation(self, all_text: str) -> QualityCheck:
        """効果試算根拠チェック: 反応率×件数×単価の計算構造が含まれているか確認。"""
        calc_patterns = [
            r"反応率[\s\d.%％×✕]+",
            r"[\d,]+[通部枚件].*[×✕].*反応率",
            r"[\d,]+[通部枚件].*[×✕].*[\d.]+%",
            r"[\d.]+%.*[×✕=＝].*[\d,]+[件円]",
            r"[×✕].*単価.*[×✕=＝]",
            r"[\d,]+件[×✕][\d,]+円",
            r"月間?[\d,]+件.*[×✕].*[\d,]+円",
        ]

        found_patterns = [
            pattern for pattern in calc_patterns
            if re.search(pattern, all_text)
        ]

        if found_patterns:
            return QualityCheck(
                check_type="adoption_pattern",
                target="効果試算根拠",
                status="pass",
                message="効果試算根拠OK: 計算式の構造を含む",
            )
        else:
            return QualityCheck(
                check_type="adoption_pattern",
                target="効果試算根拠",
                status="warning",
                message="効果試算に「反応率×件数×単価」等の計算式が見つかりません（-10点相当）",
                auto_fixable=False,
                fix_suggestion="「DM5,000通×反応率3%＝150件×平均単価14,400円＝216万円」のような計算式を追加してください",
            )

    def _check_differentiation(self, all_text: str, extra_patterns: list[str] | None = None) -> QualityCheck:
        """差別化明示チェック: 競合との違いが一文で表現されているか確認。"""
        diff_patterns = [
            r"ではなく.{2,20}に特化",
            r"とは異なり",
            r"と差別化",
            r"に対し(て|、)当(社|店)",
            r"(他社|他店|競合).*(中|一方).*(当社|当店)",
            r"(当社|当店)ならでは",
            r"唯一の",
            r"(〇〇|他社|他店|競合)が.*(中|一方|のに対し)",
        ]

        # 注入スキルからの追加パターン（キーワード形式 → 存在チェックに変換）
        if extra_patterns:
            for pat in extra_patterns:
                if pat not in diff_patterns:
                    diff_patterns.append(re.escape(pat))

        found = any(re.search(pattern, all_text) for pattern in diff_patterns)

        if found:
            return QualityCheck(
                check_type="adoption_pattern",
                target="差別化明示",
                status="pass",
                message="差別化表現OK: 競合との違いを明示する表現を含む",
            )
        else:
            return QualityCheck(
                check_type="adoption_pattern",
                target="差別化明示",
                status="warning",
                message="競合との差別化が一文で明示されていません（-5点相当）",
                auto_fixable=False,
                fix_suggestion="「〇〇ではなく△△」という形式で、競合との違いを1文で言い切る表現を追加してください",
            )

    def _check_industry_classification(self, all_text: str, story: dict) -> QualityCheck | None:
        """業種分類整合チェック: 「主たる業種」と日本標準産業分類の大分類が矛盾していないか確認。"""
        # ストーリーから「主たる業種」の記載を探す
        industry_pattern = r"主たる業種[：:は]\s*(.+?)[\n。、]"
        match = re.search(industry_pattern, all_text)
        if not match:
            return None

        stated_industry = match.group(1).strip()

        # 事業内容からキーワードで実際の業種を推定
        detected_industries: list[str] = []
        for classification, keywords in self.INDUSTRY_CLASSIFICATION.items():
            for kw in keywords:
                if kw in all_text:
                    detected_industries.append(classification)
                    break

        if not detected_industries:
            return None

        # 宣言された業種にマッチする検出業種と、マッチしない検出業種を分離
        matched: list[str] = []
        unmatched: list[str] = []
        for detected in detected_industries:
            is_match = False
            if detected in stated_industry:
                is_match = True
            else:
                for kw in self.INDUSTRY_CLASSIFICATION.get(detected, []):
                    if kw in stated_industry:
                        is_match = True
                        break
            if is_match:
                matched.append(detected)
            else:
                unmatched.append(detected)

        # マッチしない業種がある場合は不整合の可能性を警告
        if unmatched:
            return QualityCheck(
                check_type="adoption_pattern",
                target="業種分類整合",
                status="warning",
                message=f"業種分類の不整合の可能性: 「{stated_industry}」と記載されていますが、"
                        f"事業内容からは{', '.join(unmatched[:3])}にも該当する可能性があります",
                auto_fixable=False,
                fix_suggestion="日本標準産業分類の大分類を確認し、「主たる業種」の記載を修正してください",
            )
        else:
            return QualityCheck(
                check_type="adoption_pattern",
                target="業種分類整合",
                status="pass",
                message=f"業種分類OK: 「{stated_industry}」",
            )

    # ------------------------------------------------------------------
    # 6. 論理的一貫性チェック（Claude API）
    # ------------------------------------------------------------------

    async def _check_consistency(self, story: dict) -> QualityCheck:
        """ストーリーの論理的一貫性をClaude APIで検証する。"""
        sections = story.get("sections", {})

        # セクションテキストを結合
        section_texts = []
        for name in ["current_state", "ideal_state", "challenges", "solution", "expected_effect"]:
            section = sections.get(name, {})
            text = section.get("text", "")
            if text:
                section_texts.append(f"【{name}】\n{text}")

        if not section_texts:
            return QualityCheck(
                check_type="consistency",
                target="ストーリー全体",
                status="warning",
                message="ストーリーのテキストが空のため、一貫性チェックを実行できません",
            )

        combined_text = "\n\n".join(section_texts)

        system_prompt = (
            "あなたは補助金申請書の品質レビュアーです。\n"
            "提出される申請書ストーリーの論理的一貫性を評価してください。\n\n"
            "以下の観点でチェックし、JSON形式で結果を返してください：\n"
            "1. 現状→課題→解決策→効果の論理的つながり\n"
            "2. 数値や事実の矛盾がないか\n"
            "3. 解決策が課題に対応しているか\n\n"
            "出力形式（JSON）:\n"
            '{"is_consistent": true/false, "issues": ["問題点1", "問題点2"], "summary": "総評"}'
        )

        user_message = f"以下の申請書ストーリーの論理的一貫性をチェックしてください。\n\n{combined_text}"

        try:
            response = await call_claude(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
            )

            result = parse_json_response(response)
            is_consistent = result.get("is_consistent", True)
            issues = result.get("issues", [])
            summary = result.get("summary", "")

            if is_consistent:
                return QualityCheck(
                    check_type="consistency",
                    target="ストーリー全体",
                    status="pass",
                    message=f"論理的一貫性OK: {summary}" if summary else "論理的一貫性OK",
                )
            else:
                issue_text = "; ".join(issues) if issues else summary
                return QualityCheck(
                    check_type="consistency",
                    target="ストーリー全体",
                    status="warning",
                    message=f"論理的一貫性に課題あり: {issue_text}",
                    fix_suggestion=summary if summary else None,
                )

        except Exception as e:
            self.logger.warning("一貫性チェックでエラーが発生: %s", e)
            return QualityCheck(
                check_type="consistency",
                target="ストーリー全体",
                status="warning",
                message=f"一貫性チェックを実行できませんでした: {e}",
            )

    # ------------------------------------------------------------------
    # 7. 前回不採択からの改善チェック（再申請時のみ、Claude API）
    # ------------------------------------------------------------------

    async def _check_past_rejection_improvements(
        self,
        story: dict,
        past_rejection: "PastRejectionContext",  # noqa: F821 — imported lazily inside the method
    ) -> list[QualityCheck]:
        """前回の不採択理由が今回の申請書で改善されているかをClaude APIで検証する。"""

        sections = story.get("sections", {})
        all_text = self._extract_all_text(sections)

        if not all_text:
            return [
                QualityCheck(
                    check_type="past_rejection_improvement",
                    target="再申請改善",
                    status="warning",
                    message="ストーリーが空のため、前回不採択からの改善チェックを実行できません",
                )
            ]

        rejection_list = "\n".join(
            f"{i}. {reason}"
            for i, reason in enumerate(past_rejection.rejection_reasons, 1)
        )

        consultant_context = ""
        if past_rejection.consultant_notes:
            consultant_context = f"\n\n## コンサルの振り返りメモ\n{past_rejection.consultant_notes}"

        system_prompt = (
            "あなたは補助金申請書の品質レビュアーです。\n"
            "この案件は**再申請**です。前回の不採択理由に対して、"
            "今回の申請書で十分に改善されているかを厳密にチェックしてください。\n\n"
            "各不採択理由について以下をJSON配列で返してください:\n"
            "```json\n"
            "[\n"
            "  {\n"
            '    "rejection_reason": "前回の不採択理由",\n'
            '    "improved": true/false,\n'
            '    "evidence": "改善が確認できた箇所の具体的な引用や説明",\n'
            '    "suggestion": "未改善の場合の改善提案（improvedがtrueなら空文字）"\n'
            "  }\n"
            "]\n"
            "```"
        )

        user_message = (
            f"## 前回の不採択理由\n{rejection_list}"
            f"{consultant_context}\n\n"
            f"## 今回の申請書ストーリー\n{all_text}"
        )

        try:
            response = await call_claude(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
            )

            results = parse_json_response(response)
            if not isinstance(results, list):
                results = [results]

            checks: list[QualityCheck] = []
            for item in results:
                reason = item.get("rejection_reason", "不明")
                improved = item.get("improved", False)
                evidence = item.get("evidence", "")
                suggestion = item.get("suggestion", "")

                if improved:
                    checks.append(
                        QualityCheck(
                            check_type="past_rejection_improvement",
                            target=f"再申請改善: {reason[:30]}",
                            status="pass",
                            message=f"改善確認OK: {evidence[:100]}",
                        )
                    )
                else:
                    checks.append(
                        QualityCheck(
                            check_type="past_rejection_improvement",
                            target=f"再申請改善: {reason[:30]}",
                            status="fail",
                            message=f"前回の不採択理由「{reason}」が十分に改善されていません",
                            auto_fixable=False,
                            fix_suggestion=suggestion or f"「{reason}」に対する改善内容を明確に記述してください",
                        )
                    )

            return checks

        except Exception as e:
            self.logger.warning("前回不採択改善チェックでエラーが発生: %s", e)
            return [
                QualityCheck(
                    check_type="past_rejection_improvement",
                    target="再申請改善",
                    status="warning",
                    message=f"前回不採択改善チェックを実行できませんでした: {e}",
                )
            ]
