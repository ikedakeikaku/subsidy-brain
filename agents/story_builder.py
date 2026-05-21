"""#8 ストーリービルダー

審査基準から逆算して申請書のストーリーを構築する。
電子申請フォームの階層構造（経営計画・補助事業計画・加点）に対応。
現状 → 理想 → 課題 → 解決策 → 効果 の流れを一貫性を持って生成。
"""

import json
import logging
import re

from agents.base import BaseAgent
from schemas.section_limits import get_target_chars
from schemas.story import (
    BonusPointSections,
    ConsistencyCheck,
    DataSources,
    ExpenseMapping,
    ManagementPlanSections,
    ScoringAlignment,
    SolutionSection,
    StoryBuilderInput,
    StoryBuilderOutput,
    StorySection,
    StorySections,
    StorySectionWithBold,
    StorySectionWithTable,
    StorySectionWithTableAndBold,
    SubsidyPlanSections,
)
from tools.claude_client import call_claude, parse_json_response
from tools.skill_store import skill_store

logger = logging.getLogger(__name__)


class StoryBuilder(BaseAgent):
    """審査基準逆算型ストーリー構築エージェント（電子申請フォーム対応版）"""

    agent_id = "#8"
    agent_name = "ストーリービルダー"
    skill_injection_target = True

    # セクションごとの目標文字数（schemas/section_limits.py の一元定義を参照）
    CHAR_LIMITS = get_target_chars()

    # セクション文字数超過時の最大圧縮リトライ回数
    MAX_CONDENSE_RETRIES = 2

    async def _execute_impl(self, input_data: StoryBuilderInput) -> StoryBuilderOutput:
        """メイン処理。actionに応じて分岐。"""
        self.logger.info(
            "[%s] action=%s, applicant=%s",
            self.agent_id,
            input_data.action,
            input_data.applicant_id,
        )

        if input_data.action == "init":
            return await self._init_story(input_data)
        elif input_data.action == "update":
            return await self._update_story(input_data)
        elif input_data.action == "revalidate":
            return await self._revalidate_story(input_data)
        else:
            raise ValueError(f"不明なaction: {input_data.action}")

    # ------------------------------------------------------------------
    # init: 初回ストーリー生成
    # ------------------------------------------------------------------

    async def _init_story(self, input_data: StoryBuilderInput) -> StoryBuilderOutput:
        ds = input_data.data_sources

        # 1. has_efficiency をヒアリングデータから自動判定
        has_efficiency = self._detect_has_efficiency(ds.hearing)

        # 2. ストーリー本文を生成
        system_prompt = self._build_init_system_prompt(has_efficiency, input_data)
        user_message = self._build_init_user_message(ds)

        raw_response = await call_claude(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.3,
            max_tokens=16384,
        )
        story_data = parse_json_response(raw_response)
        sections = self._parse_sections(story_data, has_efficiency)

        # 3. 文字数チェック＆圧縮
        sections = await self._check_char_limits(sections)

        # 4. 審査基準との整合性チェック
        scoring_alignment = await self._build_scoring_alignment(
            sections, ds.scoring_criteria
        )

        # 5. 一貫性チェック
        consistency = await self._check_consistency(
            sections, ds.expenses, ds.financial
        )

        return StoryBuilderOutput(
            story_version=1,
            status="draft",
            sections=sections,
            scoring_alignment=scoring_alignment,
            consistency_check=consistency,
        )

    # ------------------------------------------------------------------
    # update: 人的フィードバックを反映
    # ------------------------------------------------------------------

    async def _update_story(self, input_data: StoryBuilderInput) -> StoryBuilderOutput:
        feedback = input_data.human_feedback
        if feedback is None:
            raise ValueError("update アクションにはhuman_feedbackが必要です")

        ds = input_data.data_sources

        # 既存セクションをdata_sourcesから復元（前回出力がhearing等に格納されている想定）
        existing_story = ds.hearing.get("current_story", {})
        current_version = existing_story.get("story_version", 1)
        existing_sections_data = existing_story.get("sections", {})

        has_efficiency = self._detect_has_efficiency(ds.hearing)

        # 修正対象セクション以外はそのまま維持
        revision_map = {r.section: r.comment for r in feedback.revision_requests}

        # 既存データから全セクションキーを収集（management_plan + subsidy_plan のフラット辞書）
        flat_existing = self._flatten_sections_data(existing_sections_data)

        sections_dict: dict = {}
        revision_diffs: list[dict] = []  # 修正パターン蓄積用
        for section_name in self.CHAR_LIMITS:
            if section_name in revision_map:
                before_data = flat_existing.get(section_name, {})
                # このセクションは修正コメントに従って再生成
                revised = await self._revise_section(
                    section_name,
                    before_data,
                    revision_map[section_name],
                    ds,
                )
                sections_dict[section_name] = revised
                # 修正前後の差分を記録
                revision_diffs.append({
                    "section": section_name,
                    "comment": revision_map[section_name],
                    "before_text": before_data.get("text", ""),
                    "after_text": revised.get("text", ""),
                })
            else:
                # 承認済みまたは変更なし: 既存を維持
                sections_dict[section_name] = flat_existing.get(section_name, {})

        sections = self._parse_sections(sections_dict, has_efficiency)
        sections = await self._check_char_limits(sections)

        scoring_alignment = await self._build_scoring_alignment(
            sections, ds.scoring_criteria
        )
        consistency = await self._check_consistency(
            sections, ds.expenses, ds.financial
        )

        # 修正パターンを Skill Store に蓄積（非同期・失敗しても処理継続）
        if revision_diffs:
            industry = ds.hearing.get("industry")
            try:
                from agents.revision_harvester import harvest_revisions_batch
                await harvest_revisions_batch(
                    applicant_id=input_data.applicant_id,
                    agent_id=self.agent_id,
                    revisions=revision_diffs,
                    industry=industry,
                )
            except Exception as e:
                logger.warning("修正パターン蓄積に失敗（処理は継続）: %s", e)

        return StoryBuilderOutput(
            story_version=current_version + 1,
            status="human_review",
            sections=sections,
            scoring_alignment=scoring_alignment,
            consistency_check=consistency,
        )

    # ------------------------------------------------------------------
    # revalidate: 新データとの整合性を再検証
    # ------------------------------------------------------------------

    async def _revalidate_story(
        self, input_data: StoryBuilderInput
    ) -> StoryBuilderOutput:
        ds = input_data.data_sources
        existing_story = ds.hearing.get("current_story", {})
        current_version = existing_story.get("story_version", 1)
        existing_sections_data = existing_story.get("sections", {})

        has_efficiency = self._detect_has_efficiency(ds.hearing)
        flat_existing = self._flatten_sections_data(existing_sections_data)
        sections = self._parse_sections(flat_existing, has_efficiency)

        # 新データ（market_data, fact_check）との整合性を確認
        system_prompt = (
            "あなたは補助金申請書の品質管理の専門家です。\n"
            "既存のストーリーと新しいデータを比較し、不整合がないか確認してください。\n"
            "JSON形式で回答してください:\n"
            '{"is_consistent": true/false, "issues": ["不整合点1", ...]}'
        )

        new_data: dict = {}
        if ds.market_data:
            new_data["market_data"] = ds.market_data
        if ds.fact_check:
            new_data["fact_check"] = ds.fact_check

        user_message = (
            f"## 既存ストーリー\n{json.dumps(existing_sections_data, ensure_ascii=False)}\n\n"
            f"## 新しいデータ\n{json.dumps(new_data, ensure_ascii=False)}"
        )

        raw_response = await call_claude(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
        )
        result = parse_json_response(raw_response)

        is_consistent = result.get("is_consistent", True)
        issues = result.get("issues", [])

        scoring_alignment = await self._build_scoring_alignment(
            sections, ds.scoring_criteria
        )

        status = "approved" if is_consistent else "needs_revision"

        return StoryBuilderOutput(
            story_version=current_version,
            status=status,
            sections=sections,
            scoring_alignment=scoring_alignment,
            consistency_check=ConsistencyCheck(
                is_consistent=is_consistent,
                issues=issues,
            ),
        )

    # ------------------------------------------------------------------
    # has_efficiency 自動判定
    # ------------------------------------------------------------------

    def _detect_has_efficiency(self, hearing: dict) -> bool:
        """ヒアリングデータから業務効率化取組の有無を自動判定する。"""
        if not hearing:
            return False

        efficiency_keywords = [
            "効率化", "効率", "IT", "システム", "ツール", "デジタル",
            "自動化", "省力化", "DX", "ソフトウェア", "クラウド",
            "業務改善", "生産性", "POSレジ", "会計ソフト", "予約システム",
            "在庫管理", "RPA", "ERP", "CRM",
        ]

        hearing_str = json.dumps(hearing, ensure_ascii=False)
        return any(kw in hearing_str for kw in efficiency_keywords)

    # ------------------------------------------------------------------
    # フラット辞書ヘルパー（update / revalidate 用）
    # ------------------------------------------------------------------

    def _flatten_sections_data(self, sections_data: dict) -> dict:
        """StorySectionsをシリアライズした入れ子辞書をフラット辞書に変換する。

        sections_data は StorySections.model_dump() 相当の入れ子構造
        （management_plan / subsidy_plan / bonus_points を持つ）か、
        または既にフラット辞書のどちらかを受け入れる。
        """
        if not sections_data:
            return {}

        # 既にフラット（sec_1_1 等のキーが直接ある）場合はそのまま返す
        if any(k in sections_data for k in ("sec_1_1", "subsidy_project_name")):
            return sections_data

        flat: dict = {}
        mp = sections_data.get("management_plan", {})
        if mp:
            for key in (
                "sec_1_1", "sec_1_2", "sec_1_3",
                "sec_2_1", "sec_2_2",
                "sec_3",
                "sec_4_1", "sec_4_2",
            ):
                if key in mp:
                    flat[key] = mp[key]

        sp = sections_data.get("subsidy_plan", {})
        if sp:
            for key in (
                "subsidy_project_name",
                "subsidy_2_1", "subsidy_2_2", "subsidy_2_3",
                "subsidy_3_1", "subsidy_3_2",
                "subsidy_4_1", "subsidy_4_2",
            ):
                if key in sp:
                    flat[key] = sp[key]

        bp = sections_data.get("bonus_points", {})
        if bp:
            for key in ("bonus_env_change", "bonus_local", "consultant_memo"):
                if key in bp:
                    flat[key] = bp[key]

        return flat

    # ------------------------------------------------------------------
    # プロンプト構築
    # ------------------------------------------------------------------

    def _build_init_system_prompt(self, has_efficiency: bool, input_data: "StoryBuilderInput | None" = None) -> str:
        efficiency_section_instructions = ""
        if has_efficiency:
            efficiency_section_instructions = (
                '\n\n#### subsidy_3_1（業務効率化の取組 背景・目的）— 約500字\n'
                '以下を必ず含めること:\n'
                '- 現状の業務プロセスにおける非効率な点・ボトルネックの具体的説明\n'
                '- 非効率が経営に与えている影響（時間ロス・コスト・機会損失）\n'
                '- 業務効率化に取り組む必要性・緊急性\n'
                '- 改善後に目指す業務の姿\n'
                '- 審査加点キーワード: 生産性向上、IT活用、デジタル化、業務改革\n\n'
                '#### subsidy_3_2（業務効率化の取組 具体的な取組）— 約800字\n'
                '以下を必ず含めること:\n'
                '- 導入するシステム・ツール・仕組みの名称と具体的な機能説明\n'
                '- 現状の業務フローと導入後の業務フローの比較\n'
                '- 具体的な改善効果（時間削減: XX時間/月、コスト削減: XX万円/年など定量値）\n'
                '- 実施手順（ステップ1〜3など）とスケジュール\n'
                '- expense_mappingで各経費項目がどの効率化課題を解決するか対応付け\n'
                '- セキュリティ対策・データバックアップ方針への言及（ITツールの場合）\n'
            )

        efficiency_json_example = ""
        if has_efficiency:
            efficiency_json_example = (
                '  "subsidy_3_1": {\n'
                '    "text": "（約500字の業務効率化の背景・目的）",\n'
                '    "key_points": ["非効率ポイント1", "改善後の姿"],\n'
                '    "data_references": ["現状: 月XX時間のロス"]\n'
                '  },\n'
                '  "subsidy_3_2": {\n'
                '    "text": "（約800字の業務効率化の具体的取組）",\n'
                '    "expense_mapping": [\n'
                '      {"expense_item": "経費項目名", "solves_challenge": "解決する効率化課題", "estimate_id": ""}\n'
                '    ]\n'
                '  },\n'
            )
        else:
            efficiency_json_example = (
                '  "subsidy_3_1": null,\n'
                '  "subsidy_3_2": null,\n'
            )

        return (
            "あなたは小規模事業者持続化補助金の申請書作成の専門家です。\n"
            "過去200件以上の採択実績がある行政書士として、審査基準から逆算し、\n"
            "高得点が取れる申請書の全文を生成してください。\n\n"

            "## 全体方針\n"
            "- 経営計画書（合計約4,500字）と補助事業計画書（合計約5,500字）を生成する\n"
            "- 具体的な数値・固有名詞・時期を必ず含める。「約」「程度」は使用禁止。概算でも端数を丸めた具体値で書く\n"
            "  （例: 「約7割」→「72%」、「約130万」→「132万」）\n"
            "- 保守係数は使わない。売上試算は「顧客数×比率×単価＝売上」のストレートな計算のみ\n"
            "- 「〜と考えられる」「〜と思われる」等の曖昧表現を避け、断定的に書く\n"
            "- 審査加点キーワードを自然に盛り込む: 販路開拓、生産性向上、地域活性化、\n"
            "  顧客満足度向上、差別化戦略、デジタル活用、経営基盤強化\n"
            "- 各セクション間で論理的な一貫性（現状→課題→解決→効果の因果関係）を保つ\n"
            "- 各セクションのtextフィールドは目標文字数の90%以上を必ず満たすこと\n\n"

            "## IT活用（コスト0円）施策の織り込みルール（必須）\n"
            "審査基準「デジタル技術を有効的に活用する取組等が見られるか」に対応するため、\n"
            "コスト0円のIT活用施策を以下の5箇所に分散配置すること:\n"
            "  1. subsidy_2_1（事業の概要）— IT活用を含む全体像に言及\n"
            "  2. subsidy_2_3（具体的な取組）— (D) IT活用【コスト0円】セクションとして記載\n"
            "  3. subsidy_3_2（業務効率化の具体的な取組）— LINE管理・Google最適化・転換導線\n"
            "  4. subsidy_4_1（取組の効果）— デジタル技術活用による効果を必ず含める\n"
            "  5. sec_4_2（今後のプラン）— IT施策の継続・発展計画に言及\n"
            "事業者に適した施策を選択（全部入れなくてよい）:\n"
            "  - LINE公式アカウント（日程リマインド・申込受付・休眠顧客フォロー）\n"
            "  - チラシにQRコード掲載（LINE友だち追加用・Googleマップ表示用）\n"
            "  - Googleビジネスプロフィール最適化（ローカル検索強化・口コミ対応）\n"
            "  - Instagram/SNS活用（ビジュアル訴求が有効な業種向け）\n"
            "  - Googleフォーム（予約・アンケート受付のデジタル化）\n\n"

            "## 出典・データ引用ルール（厳守）\n"
            "- sec_2_1（市場の動向）・sec_2_2（顧客ニーズ）では、公的機関の統計データを\n"
            "  必ず含めて記載すること\n"
            "- 使用すべき公的データソース:\n"
            "  - 経済産業省「商業動態統計」「特定サービス産業動態統計」\n"
            "  - 中小企業庁「中小企業白書」「小規模企業白書」\n"
            "  - 総務省「家計調査」「経済センサス」\n"
            "  - 各業界団体の統計（例: 日本フードサービス協会、全国理美容製造者協会等）\n"
            "  - 観光庁「訪日外客統計」（インバウンド関連の場合）\n"
            "  - 地方自治体の統計・経済レポート（地域データの場合）\n"
            "- 出典は必ず明記すること。記載形式:\n"
            "  「（出典: 中小企業白書2025年版 p.XX）」\n"
            "  「（出典: 経済産業省 商業動態統計 2024年）」\n"
            "  「（出典: 〇〇市 経済動向レポート 2025年3月）」\n"
            "- ヒアリングデータに業種が記載されている場合、その業種に対応する\n"
            "  公的統計データを必ず1つ以上引用すること\n"
            "- data_referencesフィールドにも出典情報を格納すること\n\n"

            "## 議事録・ヒアリングデータの活用ルール\n"
            "- ヒアリングデータに「議事録」「ヒアリングメモ」「打合せ記録」等が含まれる場合、\n"
            "  顧客固有の情報（事業者名・代表者名・所在地・商品名・取引先名・\n"
            "  具体的な数値・エピソード等）を最大限に活用して記述すること\n"
            "- 議事録中の顧客の発言・要望は「顧客の声」として sec_2_2 に反映\n"
            "- 議事録中の事業者の課題認識は sec_1_3 の課題設定に直接反映\n"
            "- 議事録中の将来ビジョン・計画は sec_4_1 / sec_4_2 に反映\n"
            "- 一般論やテンプレート的な表現ではなく、その事業者ならではの\n"
            "  固有の表現・数値・エピソードを優先して使用すること\n"
            "- ヒアリングデータが不十分な場合のみ、業界一般のデータで補完する\n\n"

            "## 審査の観点（キーワード）\n"
            "以下のキーワードを念頭に各セクションを記述すること:\n"
            "- 経営計画: 自社の強みの活用、市場ニーズとの合致、経営課題の明確化、\n"
            "  持続的な経営改善への取組姿勢\n"
            "- 補助事業計画: 補助事業の有効性・効果、経費の合理性、\n"
            "  小規模事業者の販路開拓・生産性向上への貢献、事業終了後の持続性\n"
            "- 加点項目: 物価高騰への対応、地域資源活用、地域コミュニティ貢献、\n"
            "  賃上げ・雇用拡大\n\n"

            "## セクション別の指示と目標文字数\n\n"

            "### ＜経営計画書：合計約4,500字＞\n\n"

            "#### sec_1_1（自社の概要）— 約700字\n"
            "以下を必ず含めること:\n"
            "- 正式な事業者名・代表者名・所在地・創業年\n"
            "- 業種・事業内容の具体的な説明（取扱商品・サービスの詳細）\n"
            "- 従業員数（常勤・パートの内訳）\n"
            "- 主要取引先・顧客層の具体的記述\n"
            "- 直近の売上水準と主要な収益構造\n"
            "- 営業時間・立地特性・拠点情報\n"
            "- 沿革のターニングポイント（事業転換・設備投資・資格取得など）\n\n"

            "#### sec_1_2（売上・利益の状況）— 約500字\n"
            "以下を必ず含めること:\n"
            "- 直近3期の売上高・営業利益の推移を本文で説明\n"
            "- 増減の要因分析（コロナ禍・材料費高騰・競合増加等）\n"
            "- 現状の収益構造の課題（固定費の割合・季節変動等）\n"
            "- table_dataに売上推移表を格納（ヘッダー行: 年度・売上高・営業利益）\n"
            "  例: [[\"年度\",\"売上高\",\"営業利益\"],[\"令和3年度\",\"XXX万円\",\"XX万円\"],...]\n\n"

            "#### sec_1_3（経営課題）— 約500字\n"
            "以下を必ず含めること:\n"
            "- ■課題1: 〇〇（課題名）という形式で3つの課題を列挙すること\n"
            "- 各課題について: 現象（何が起きているか）→影響（経営への打撃）→根本原因の順で記述\n"
            "- 課題の緊急度・重要度を明示\n"
            "- 課題間の相互関係（連鎖する問題）にも言及\n"
            "- sec_2_1〜sec_2_2の市場環境変化と連動させた課題設定にすること\n\n"

            "#### sec_2_1（市場の動向）— 約700字\n"
            "以下を必ず含めること:\n"
            "- ターゲット市場の規模・成長性（公的機関の統計データを必ず引用）\n"
            "  例: 「〇〇業界の市場規模はXX兆円（出典: 経済産業省 特定サービス産業動態統計 2024年）」\n"
            "- 業界全体のトレンド（デジタル化・環境対応・インバウンド等）\n"
            "  トレンドも可能な限り公的データで裏付けること\n"
            "- 地域の人口動態・経済環境の変化（自治体統計・経済センサス等を引用）\n"
            "- 競合環境の分析（競合の数・特徴・価格帯）\n"
            "- 今後3〜5年の市場見通し（白書・業界団体の予測を引用）\n"
            "- bold_termsに強調すべき市場キーワードを列挙\n"
            "- data_referencesに引用した統計データの出典を全て格納すること\n"
            "  例: [\"市場規模: 2.1兆円（出典: 経済産業省 特定サービス産業動態統計 2024年）\"]\n\n"

            "#### sec_2_2（顧客ニーズ）— 約500字\n"
            "以下を必ず含めること:\n"
            "- 主要顧客層のプロフィール（年齢層・職業・生活スタイル等）\n"
            "- ニーズの変化（コロナ後・物価高騰後・ライフスタイル変化等）\n"
            "  消費者動向の裏付けとして公的統計を引用すること\n"
            "  例: 「消費者のEC利用率はXX%に達した（出典: 総務省 通信利用動向調査 2024年）」\n"
            "- 議事録・ヒアリングデータに顧客の声がある場合は、\n"
            "  そのまま引用して具体性を高めること（最低3件）\n"
            "- 未充足ニーズ（既存の競合が満たせていないニーズ）\n"
            "- 顧客ニーズとsec_1_3の課題の対応関係を明示\n\n"

            "#### sec_3（強み・弱み）— 約700字\n"
            "以下を必ず含めること:\n"
            "- 強みは「●」記号を先頭に付けて3〜4項目列挙（技術・専門性・実績・立地等）\n"
            "- 弱みは「▲」記号を先頭に付けて2〜3項目列挙（認知度・設備・資金力等）\n"
            "- 各強みは具体的な数値・実績・資格名等を用いて根拠を示す\n"
            "- 競合との明確な差別化ポイントを3つ以上示す\n"
            "- bold_termsに強調すべき強みキーワードを列挙\n"
            "- 補助事業（subsidy_2_xセクション）の取組が強みを活かす構成にすること\n\n"

            "#### sec_4_1（経営方針・目標）— 約500字\n"
            "以下を必ず含めること:\n"
            "- 3〜5年の中期ビジョン（具体的なあるべき姿）\n"
            "- 数値目標（売上目標・利益目標・顧客数目標等を年次で記載）\n"
            "- 目標達成のための3〜4つの具体的方針\n"
            "- 地域経済・雇用への貢献方針\n"
            "- sec_1_3の課題を解決する経営方針との連動性を示す\n\n"

            "#### sec_4_2（今後のプラン）— 約400字\n"
            "以下を必ず含めること:\n"
            "- 補助事業実施後の具体的な行動計画（6〜12ヶ月単位）\n"
            "- 各施策の実施時期と担当体制\n"
            "- 補助事業（subsidy計画）との連動・位置づけの明示\n"
            "- 事業終了後の自走計画と持続性の根拠\n\n"

            "### ＜補助事業計画書：合計約5,500字＞\n\n"

            "#### subsidy_project_name（補助事業名）— 30字以内\n"
            "内容が一目で伝わる簡潔な事業名。「〇〇による△△販路開拓事業」の形式を推奨。\n"
            "具体的な手法・媒体・目的を含めること。審査員の印象に残る名称にすること。\n\n"

            "#### subsidy_2_1（事業の概要）— 約400字\n"
            "以下を必ず含めること:\n"
            "- 補助事業全体の目的を1〜2文で要約\n"
            "- 実施する取組の種類（Web広告・EC・展示会・機器導入等）\n"
            "- 期待される主要な成果（販路開拓・売上増加・効率化等）\n"
            "- 補助対象経費の種類と総額の概要\n"
            "- key_pointsに取組の核心を3つ以内で列挙\n\n"

            "#### subsidy_2_2（背景・目的）— 約1,000字\n"
            "以下を必ず含めること:\n"
            "- sec_1_3の経営課題が補助事業を必要とする背景を直接接続して説明\n"
            "- 外部環境（市場変化・競合動向・物価高騰等）が事業実施を後押しする理由\n"
            "- sec_3の強みを活かすことで補助事業が成功する根拠\n"
            "- 補助事業を実施しない場合の経営リスク（機会損失・競合劣位等）\n"
            "- 補助事業の目的（定量的な目標値を含む）\n"
            "- bold_termsに審査キーワード（販路開拓・差別化・顧客獲得等）を列挙\n\n"

            "#### subsidy_2_3（具体的な取組）— 約1,500字\n"
            "以下を必ず含めること:\n"
            "- 各取組（経費項目ごと）の具体的な内容・仕様・実施方法\n"
            "- 各取組が解決する課題・期待される効果を明示\n"
            "- 実施体制（担当者・外注先・連携先）\n"
            "- bold_termsに重要な取組キーワードを列挙\n"
            "- table_dataに実施スケジュール表を格納\n"
            "  ヘッダー行: [\"取組内容\",\"実施時期（月）\",\"担当\",\"目標KPI\"]\n"
            "  各取組を行として記述（補助事業期間内、月次で詳細に）\n\n"
            + efficiency_section_instructions +
            "#### subsidy_4_1（効果）— 約700字\n"
            "以下を必ず含めること:\n"
            "- 補助事業実施後の定性的効果（ブランド力向上・顧客関係強化等）\n"
            "- 定量的効果の見込み（新規顧客数・売上増加額・リピート率等）\n"
            "- sec_4_1の経営目標達成への貢献度\n"
            "- 地域経済への波及効果（雇用・地元仕入れ・観光客誘致等）\n"
            "- bold_termsに成果指標キーワードを列挙\n\n"

            "#### subsidy_4_2（効果の試算）— 約600字\n"
            "以下を必ず含めること:\n"
            "- 売上増加・コスト削減等の数値根拠を具体的な計算式で説明\n"
            "- 前提条件（新規顧客数・客単価・購入頻度等）を明示\n"
            "- 投資回収期間の試算\n"
            "- table_dataに試算表を格納\n"
            "  ヘッダー行: [\"項目\",\"現状\",\"目標\",\"増減\"]\n"
            "  売上・顧客数・利益率等を行として記述\n\n"

            "### ＜加点項目＞\n\n"

            "#### bonus_env_change（事業環境変化加点）\n"
            "★★ 文章のみで記載すること（表・箇条書き・リスト不可）。段落の連続で構成する。★★\n"
            "以下を必ず含めること:\n"
            "- 物価高騰に限定（ウクライナ戦争・中東情勢・円安が原因）\n"
            "- 費目ごとに段落を分け、令和4年比の数値（%）と年間影響額を本文中に織り込む\n"
            "- 事業者視点の平易な文体で500字程度\n"
            "- 電気代・仕入資材・設備維持の具体的な影響額を記載\n"
            "- 保険診療の公定価格で転嫁不可等の構造的問題があれば述べる\n"
            "- 「第一に〜。第二に〜。」等の接続で構造化する\n\n"

            "#### bonus_local（地方創生型加点）\n"
            "★★ 文章のみで記載すること（表・箇条書き・リスト不可）。段落の連続で構成する。★★\n"
            "以下を必ず含めること:\n"
            "- 「地域課題→本事業の対応→期待される効果」の3段構成で連続した文章で記述\n"
            "- 地域資源（農林水産物・伝統工芸・観光資源等）の活用内容\n"
            "  または地域コミュニティ（祭り・まちづくり・福祉等）への貢献内容\n"
            "- 地域における事業者の役割・使命の説明\n"
            "- 地域経済・文化への具体的な貢献（雇用・仕入れ先・連携先等）\n"
            "- 効果は「第一に〜。第二に〜。」で接続\n\n"

            "#### consultant_memo（コンサル向け内部メモ）\n"
            "以下を記述（申請書本文には出力しない内部メモ）:\n"
            "- 採点上の注意点・重点的に強化すべきポイント\n"
            "- 弱い記述になった箇所とその理由（ヒアリングデータ不足等）\n"
            "- ヒアリングで追加確認が必要な項目\n"
            "- 代替案・オプションがある加点戦略\n\n"

            "## 採択パターン分析に基づく必須要件（9件の実案件分析結果）\n"
            "以下は採択6件・不採択3件の実案件分析から抽出された決定的要因です。必ず遵守してください。\n\n"
            "### 要件①: 自社独自データの必須化（最重要）\n"
            "- 経営計画には必ず自社独自データ（アンケート結果、来店データ、サービス別売上構成比、"
            "SNS分析等）を含めること\n"
            "- 外部統計の引用は補助的な位置づけとし、自社データを主軸にすること\n"
            "- 不採択例: 「PubMed引用のみ」「美容センサスのみ」→ 必ず自社の一次データを入れる\n\n"
            "### 要件②: 課題↔施策の一対一対応\n"
            "- 経営課題を3〜5個に分解し、各施策がどの課題を解決するかを明示すること\n"
            "- 「■課題1: 〇〇 → 施策: △△」の形式で対応関係を見える化する\n"
            "- 不採択例: 7施策が並列で課題との紐づけが不明確\n\n"
            "### 要件③: ローカルデータ優先\n"
            "- 市場動向では市区町村レベルの人口・世帯数・年齢構成を必ず含めること\n"
            "- 全国統計は「市場全体の動向」として1〜2文に留める\n"
            "- 不採択例: 全国値のみ（慢性痛有症率15.4%、空き家率13.8%等）→ 商圏データ必須\n\n"
            "### 要件④: 効果試算の算出根拠\n"
            "- 効果試算では反応率×件数×単価の計算式を明示すること\n"
            "- 例: 「DM5,000通×反応率3%＝150件×平均単価14,400円＝216万円」\n"
            "- 不採択例: 「35件→45件（+10件）」根拠なし\n\n"
            "### 要件⑤: 差別化の一文表現\n"
            "- 差別化は「〇〇ではなく△△」の一文で言い切ること\n"
            "- 例: 「他店が広く浅い品揃えの中、当店は特定分野に深く特化」\n"
            "- 不採択例: 「トータルビューティーサロン」「総合不動産会社」→ 差別化不明確\n\n"
            + self._build_adoption_knowledge_context(input_data)
            + self._build_past_application_context(input_data)
            + self._build_revision_knowledge_context(input_data) +
            "## 出力JSON構造\n"
            "必ず以下のJSON形式で出力してください。テキスト冒頭や末尾に余計な説明は不要です:\n"
            "```json\n"
            "{\n"
            '  "sec_1_1": {\n'
            '    "text": "（約700字の自社の概要）",\n'
            '    "key_points": ["要点1", "要点2", "要点3"],\n'
            '    "data_references": ["売上高: XXX万円", "従業員数: X名"]\n'
            "  },\n"
            '  "sec_1_2": {\n'
            '    "text": "（約500字の売上・利益の状況）",\n'
            '    "key_points": ["要点1", "要点2"],\n'
            '    "data_references": ["令和5年度売上: XXX万円"],\n'
            '    "table_data": [["年度","売上高","営業利益"],["令和3年度","XXX万円","XX万円"],["令和4年度","XXX万円","XX万円"],["令和5年度","XXX万円","XX万円"]]\n'
            "  },\n"
            '  "sec_1_3": {\n'
            '    "text": "■課題1: （課題名）\\n（課題の詳細）\\n■課題2: （課題名）\\n（課題の詳細）\\n■課題3: （課題名）\\n（課題の詳細）",\n'
            '    "key_points": ["課題1", "課題2", "課題3"],\n'
            '    "data_references": []\n'
            "  },\n"
            '  "sec_2_1": {\n'
            '    "text": "（約700字の市場の動向）",\n'
            '    "key_points": ["市場トレンド1", "市場トレンド2"],\n'
            '    "data_references": ["市場規模: XX億円（出典: XX）"],\n'
            '    "bold_terms": ["市場キーワード1", "市場キーワード2"]\n'
            "  },\n"
            '  "sec_2_2": {\n'
            '    "text": "（約500字の顧客ニーズ）",\n'
            '    "key_points": ["ニーズ1", "ニーズ2"],\n'
            '    "data_references": []\n'
            "  },\n"
            '  "sec_3": {\n'
            '    "text": "●強み1: （説明）\\n●強み2: （説明）\\n●強み3: （説明）\\n▲弱み1: （説明）\\n▲弱み2: （説明）",\n'
            '    "key_points": ["強み1", "強み2", "強み3"],\n'
            '    "data_references": [],\n'
            '    "bold_terms": ["強みキーワード1", "強みキーワード2"]\n'
            "  },\n"
            '  "sec_4_1": {\n'
            '    "text": "（約500字の経営方針・目標）",\n'
            '    "key_points": ["方針1", "方針2", "方針3"],\n'
            '    "data_references": ["3年後売上目標: XXX万円"]\n'
            "  },\n"
            '  "sec_4_2": {\n'
            '    "text": "（約400字の今後のプラン）",\n'
            '    "key_points": ["施策1", "施策2"],\n'
            '    "data_references": []\n'
            "  },\n"
            '  "subsidy_project_name": {\n'
            '    "text": "〇〇による△△販路開拓事業（30字以内）",\n'
            '    "key_points": [],\n'
            '    "data_references": []\n'
            "  },\n"
            '  "subsidy_2_1": {\n'
            '    "text": "（約400字の事業の概要）",\n'
            '    "key_points": ["取組1", "取組2", "期待成果"],\n'
            '    "data_references": []\n'
            "  },\n"
            '  "subsidy_2_2": {\n'
            '    "text": "（約1,000字の背景・目的）",\n'
            '    "key_points": ["背景1", "目的1", "根拠1"],\n'
            '    "data_references": [],\n'
            '    "bold_terms": ["販路開拓", "差別化", "顧客獲得"]\n'
            "  },\n"
            '  "subsidy_2_3": {\n'
            '    "text": "（約1,500字の具体的な取組の詳細説明）",\n'
            '    "key_points": ["取組内容1", "取組内容2"],\n'
            '    "data_references": [],\n'
            '    "bold_terms": ["重要キーワード1", "重要キーワード2"],\n'
            '    "table_data": [["取組内容","実施時期（月）","担当","目標KPI"],["〇〇制作","交付決定〜1ヶ月目","代表","完成納品"],["△△運用開始","2ヶ月目〜","代表","月X件問合せ"]]\n'
            "  },\n"
            + efficiency_json_example +
            '  "subsidy_4_1": {\n'
            '    "text": "（約700字の効果）",\n'
            '    "key_points": ["定性効果1", "定量効果1"],\n'
            '    "data_references": [],\n'
            '    "bold_terms": ["売上増加", "顧客獲得", "認知度向上"]\n'
            "  },\n"
            '  "subsidy_4_2": {\n'
            '    "text": "（約600字の効果の試算根拠説明）",\n'
            '    "key_points": ["試算根拠1", "投資回収計画"],\n'
            '    "data_references": [],\n'
            '    "table_data": [["項目","現状","目標","増減"],["月間売上","XX万円","XX万円","+XX万円"],["新規顧客数","X件/月","X件/月","+X件"],["客単価","X,XXX円","X,XXX円","+XXX円"],["利益率","XX%","XX%","+X%"]]\n'
            "  },\n"
            '  "bonus_env_change": {\n'
            '    "text": "（物価高騰等の外部環境変化への対応を記述）",\n'
            '    "key_points": ["影響1", "対応策1"],\n'
            '    "data_references": ["原材料費XX%上昇（20XX年比）"]\n'
            "  },\n"
            '  "bonus_local": {\n'
            '    "text": "（地域資源活用または地域コミュニティへの貢献を記述）",\n'
            '    "key_points": ["地域貢献1", "地域貢献2"],\n'
            '    "data_references": []\n'
            "  },\n"
            '  "consultant_memo": "（採点注意点・追加ヒアリング必要事項・加点戦略等の内部メモ）"\n'
            "}\n"
            "```\n\n"
            "重要: 各セクションのtextフィールドは必ず目標文字数の90%以上を満たすこと。\n"
            "短すぎる文章は審査で減点される。JSONのみを出力し、前後に説明文を付けないこと。"
        )

    def _build_adoption_knowledge_context(self, input_data: "StoryBuilderInput | None" = None) -> str:
        """Skill Storeからadoption_patterns/exemplar_textsを取得しプロンプトに注入する。

        search_similar_skillsを使い、industry="common"のパターンと
        業種一致のexemplar_textsの両方を取得する。
        """
        parts: list[str] = []

        # adoption_patternsの取得（common）
        adoption = skill_store.get_knowledge("adoption_patterns")
        if adoption:
            factors = adoption.content.get("factors", [])
            if factors:
                parts.append("## 採択パターン詳細（Skill Store参照）\n")
                for f in factors:
                    name = f.get("name", "")
                    examples = f.get("positive_examples", [])[:2]
                    if examples:
                        parts.append(f"### {name}")
                        for ex in examples:
                            parts.append(f"- 例: {ex}")
                        parts.append("")

        # 業種を推定してexemplar_textsを検索
        industry = None
        if input_data and input_data.data_sources and input_data.data_sources.hearing:
            industry = input_data.data_sources.hearing.get("industry")

        # search_similar_skillsで業種一致 + common のスキルを取得
        similar_skills = skill_store.search_similar_skills(
            "#8", {"industry": industry} if industry else None
        )

        # exemplar_textsを持つスキルを抽出
        exemplar_skills = [
            s for s in similar_skills
            if s.skill_type.value == "knowledge"
            and s.content.get("name", "").startswith("exemplar_texts")
        ]

        if exemplar_skills:
            parts.append("## 採択案件の模範テキスト（few-shot参考）\n")
            for skill in exemplar_skills:
                industry_label = skill.industry or "汎用"
                exemplars = skill.content.get("exemplars", {})
                for _category, data in exemplars.items():
                    desc = data.get("description", "")
                    examples = data.get("examples", [])[:1]
                    if examples:
                        parts.append(f"### {desc}（{industry_label}）")
                        for ex in examples:
                            biz = ex.get("business", "")
                            text = ex.get("text", "")
                            parts.append(f"【{biz}】{text}")
                        parts.append("")

        if parts:
            return "\n".join(parts) + "\n"
        return ""

    def _build_past_application_context(self, input_data: "StoryBuilderInput | None" = None) -> str:
        """前回申請の情報をプロンプトに注入する（再申請時のみ）。"""
        if not input_data or not input_data.data_sources.past_application:
            return ""

        past = input_data.data_sources.past_application
        parts = [
            "## 前回申請からの改善指示（再申請）\n",
            f"この案件は**再申請**です（前回: {past.submission_round or '不明'}, 結果: {past.result}）。\n",
            "前回の不採択を踏まえ、以下の改善を必ず反映してください。\n",
        ]

        if past.rejection_reasons:
            parts.append("### 前回の不採択理由")
            for i, reason in enumerate(past.rejection_reasons, 1):
                parts.append(f"{i}. {reason}")
            parts.append("")
            parts.append(
                "**上記の各不採択理由に対して、今回の申請書で明確に改善・対応した内容を盛り込むこと。**\n"
            )

        if past.consultant_notes:
            parts.append(f"### コンサルの振り返りメモ\n{past.consultant_notes}\n")

        parts.append(
            "### 再申請における重要ルール\n"
            "- 前回と同じ表現・構成をそのまま使い回さない。審査員に改善努力が伝わるよう構成を見直す\n"
            "- 不採択理由で指摘された箇所は、具体的なデータ・根拠を追加して補強する\n"
            "- 前回弱かったセクションは文字数を増やし、説得力を高める\n"
            "- consultant_memo に「前回からの主な改善点」を箇条書きで記載する\n"
        )

        return "\n".join(parts) + "\n"

    def _build_revision_knowledge_context(self, input_data: "StoryBuilderInput | None" = None) -> str:
        """蓄積された修正パターンをプロンプトに注入する。"""
        try:
            from agents.revision_harvester import build_revision_knowledge_prompt
            industry = None
            if input_data and input_data.data_sources and input_data.data_sources.hearing:
                industry = input_data.data_sources.hearing.get("industry")
            return build_revision_knowledge_prompt(self.agent_id, industry)
        except Exception:
            return ""

    def _build_init_user_message(self, ds: DataSources) -> str:
        parts = ["## ストーリー構築に必要なデータ\n"]

        if ds.hearing:
            # 議事録・ヒアリングメモが含まれているか検出
            hearing_str = json.dumps(ds.hearing, ensure_ascii=False)
            has_minutes = any(
                kw in hearing_str
                for kw in ["議事録", "ヒアリングメモ", "打合せ記録", "面談記録", "相談内容"]
            )
            header = "### ヒアリングデータ"
            if has_minutes:
                header += "（※議事録を含む — 顧客固有の情報を最大限活用すること）"
            parts.append(
                f"{header}\n{json.dumps(ds.hearing, ensure_ascii=False, indent=2)}\n"
            )

        if ds.financial:
            parts.append(
                f"### 決算書データ（#6出力）\n{json.dumps(ds.financial, ensure_ascii=False, indent=2)}\n"
            )

        if ds.expenses:
            parts.append(
                f"### 経費明細（#7出力）\n{json.dumps(ds.expenses, ensure_ascii=False, indent=2)}\n"
            )

        if ds.scoring_criteria:
            criteria_text = json.dumps(ds.scoring_criteria, ensure_ascii=False, indent=2)
            parts.append(f"### 審査基準（#5出力）\n{criteria_text}\n")

        if ds.market_data:
            parts.append(
                f"### 市場分析データ（#10出力）\n{json.dumps(ds.market_data, ensure_ascii=False, indent=2)}\n"
            )

        if ds.fact_check:
            parts.append(
                f"### ファクトチェック結果（#12出力）\n{json.dumps(ds.fact_check, ensure_ascii=False, indent=2)}\n"
            )

        if ds.past_application and ds.past_application.past_story:
            parts.append(
                f"### 前回の申請書ストーリー（参考・改善元）\n"
                f"{json.dumps(ds.past_application.past_story, ensure_ascii=False, indent=2)}\n"
                f"※上記は前回不採択となった内容です。同じ表現を使い回さず、改善してください。\n"
            )

        if ds.past_application:
            parts.append(
                "\n上記のデータを元に、**前回の不採択理由を踏まえて改善した**ストーリーをJSON形式で生成してください。"
            )
        else:
            parts.append(
                "\n上記のデータを元に、審査基準から逆算して高得点を取れるストーリーをJSON形式で生成してください。"
            )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # セクション修正（update用）
    # ------------------------------------------------------------------

    async def _revise_section(
        self,
        section_name: str,
        existing_data: dict,
        comment: str,
        ds: DataSources,
    ) -> dict:
        """指定セクションを修正コメントに従って再生成する。"""
        char_limit = self.CHAR_LIMITS.get(section_name, 800)

        system_prompt = (
            "あなたは補助金申請書の修正担当者です。\n"
            f"指定されたセクション「{section_name}」を修正コメントに従って書き直してください。\n"
            f"文字数は{char_limit}文字以内に収めてください。\n"
            "JSON形式で修正後のセクションを出力してください。余計な説明は不要です。"
        )

        user_message = (
            f"## 修正対象セクション: {section_name}\n"
            f"### 現在の内容\n{json.dumps(existing_data, ensure_ascii=False, indent=2)}\n\n"
            f"### 修正コメント\n{comment}\n\n"
            "### 参考データ\n"
            f"ヒアリング: {json.dumps(ds.hearing, ensure_ascii=False)}\n"
            f"決算書: {json.dumps(ds.financial, ensure_ascii=False)}\n"
            f"経費: {json.dumps(ds.expenses, ensure_ascii=False)}\n\n"
            "修正後のセクションをJSON形式で出力してください。"
        )

        raw_response = await call_claude(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.3,
        )
        return parse_json_response(raw_response)

    # ------------------------------------------------------------------
    # セクションパース
    # ------------------------------------------------------------------

    def _parse_sections(self, data: dict, has_efficiency: bool) -> StorySections:
        """辞書データからStorySectionsを構築する（新階層構造対応）。"""

        def _d(key: str) -> dict:
            val = data.get(key, {})
            if isinstance(val, str):
                return {"text": val}
            return val if isinstance(val, dict) else {}

        def _str(key: str) -> str:
            val = data.get(key, "")
            if isinstance(val, dict):
                return val.get("text", "")
            return str(val) if val else ""

        # ---- 経営計画 ----
        d_1_1 = _d("sec_1_1")
        d_1_2 = _d("sec_1_2")
        d_1_3 = _d("sec_1_3")
        d_2_1 = _d("sec_2_1")
        d_2_2 = _d("sec_2_2")
        d_3   = _d("sec_3")
        d_4_1 = _d("sec_4_1")
        d_4_2 = _d("sec_4_2")

        management_plan = ManagementPlanSections(
            sec_1_1=StorySection(
                text=d_1_1.get("text", ""),
                key_points=d_1_1.get("key_points", []),
                data_references=d_1_1.get("data_references", []),
            ),
            sec_1_2=StorySectionWithTable(
                text=d_1_2.get("text", ""),
                key_points=d_1_2.get("key_points", []),
                data_references=d_1_2.get("data_references", []),
                table_data=d_1_2.get("table_data", []),
            ),
            sec_1_3=StorySection(
                text=d_1_3.get("text", ""),
                key_points=d_1_3.get("key_points", []),
                data_references=d_1_3.get("data_references", []),
            ),
            sec_2_1=StorySectionWithBold(
                text=d_2_1.get("text", ""),
                key_points=d_2_1.get("key_points", []),
                data_references=d_2_1.get("data_references", []),
                bold_terms=d_2_1.get("bold_terms", []),
            ),
            sec_2_2=StorySection(
                text=d_2_2.get("text", ""),
                key_points=d_2_2.get("key_points", []),
                data_references=d_2_2.get("data_references", []),
            ),
            sec_3=StorySectionWithBold(
                text=d_3.get("text", ""),
                key_points=d_3.get("key_points", []),
                data_references=d_3.get("data_references", []),
                bold_terms=d_3.get("bold_terms", []),
            ),
            sec_4_1=StorySection(
                text=d_4_1.get("text", ""),
                key_points=d_4_1.get("key_points", []),
                data_references=d_4_1.get("data_references", []),
            ),
            sec_4_2=StorySection(
                text=d_4_2.get("text", ""),
                key_points=d_4_2.get("key_points", []),
                data_references=d_4_2.get("data_references", []),
            ),
        )

        # ---- 補助事業計画 ----
        d_pn  = _d("subsidy_project_name")
        d_2_1s = _d("subsidy_2_1")
        d_2_2s = _d("subsidy_2_2")
        d_2_3s = _d("subsidy_2_3")
        d_4_1s = _d("subsidy_4_1")
        d_4_2s = _d("subsidy_4_2")

        # 業務効率化セクション
        subsidy_3_1 = None
        subsidy_3_2 = None

        if has_efficiency:
            d_3_1 = _d("subsidy_3_1")
            d_3_2 = _d("subsidy_3_2")

            if d_3_1.get("text"):
                subsidy_3_1 = StorySection(
                    text=d_3_1.get("text", ""),
                    key_points=d_3_1.get("key_points", []),
                    data_references=d_3_1.get("data_references", []),
                )

            if d_3_2.get("text") or d_3_2.get("expense_mapping"):
                subsidy_3_2 = SolutionSection(
                    text=d_3_2.get("text", ""),
                    expense_mapping=[
                        ExpenseMapping(**em)
                        for em in d_3_2.get("expense_mapping", [])
                        if isinstance(em, dict)
                    ],
                )

        subsidy_plan = SubsidyPlanSections(
            subsidy_project_name=StorySection(
                text=d_pn.get("text", ""),
                key_points=d_pn.get("key_points", []),
                data_references=d_pn.get("data_references", []),
            ),
            subsidy_2_1=StorySection(
                text=d_2_1s.get("text", ""),
                key_points=d_2_1s.get("key_points", []),
                data_references=d_2_1s.get("data_references", []),
            ),
            subsidy_2_2=StorySectionWithBold(
                text=d_2_2s.get("text", ""),
                key_points=d_2_2s.get("key_points", []),
                data_references=d_2_2s.get("data_references", []),
                bold_terms=d_2_2s.get("bold_terms", []),
            ),
            subsidy_2_3=StorySectionWithTableAndBold(
                text=d_2_3s.get("text", ""),
                key_points=d_2_3s.get("key_points", []),
                data_references=d_2_3s.get("data_references", []),
                table_data=d_2_3s.get("table_data", []),
                bold_terms=d_2_3s.get("bold_terms", []),
            ),
            has_efficiency=has_efficiency,
            subsidy_3_1=subsidy_3_1,
            subsidy_3_2=subsidy_3_2,
            subsidy_4_1=StorySectionWithBold(
                text=d_4_1s.get("text", ""),
                key_points=d_4_1s.get("key_points", []),
                data_references=d_4_1s.get("data_references", []),
                bold_terms=d_4_1s.get("bold_terms", []),
            ),
            subsidy_4_2=StorySectionWithTable(
                text=d_4_2s.get("text", ""),
                key_points=d_4_2s.get("key_points", []),
                data_references=d_4_2s.get("data_references", []),
                table_data=d_4_2s.get("table_data", []),
            ),
        )

        # ---- 加点セクション ----
        d_env   = _d("bonus_env_change")
        d_local = _d("bonus_local")
        consultant_memo = _str("consultant_memo")

        bonus_points = BonusPointSections(
            bonus_env_change=StorySection(
                text=d_env.get("text", ""),
                key_points=d_env.get("key_points", []),
                data_references=d_env.get("data_references", []),
            ),
            bonus_local=StorySection(
                text=d_local.get("text", ""),
                key_points=d_local.get("key_points", []),
                data_references=d_local.get("data_references", []),
            ),
            consultant_memo=consultant_memo,
        )

        return StorySections(
            management_plan=management_plan,
            subsidy_plan=subsidy_plan,
            bonus_points=bonus_points,
        )

    # ------------------------------------------------------------------
    # 文字数チェック＆圧縮
    # ------------------------------------------------------------------

    async def _check_char_limits(self, sections: StorySections) -> StorySections:
        """各セクションの文字数を検査し、超過時はClaudeで圧縮する。"""
        mp = sections.management_plan
        sp = sections.subsidy_plan

        # セクションID → (サブモデル参照, フィールド名) のマッピング
        section_refs: list[tuple[str, object, str]] = [
            # (section_id, parent_object, field_name)
            ("sec_1_1",  mp, "sec_1_1"),
            ("sec_1_2",  mp, "sec_1_2"),
            ("sec_1_3",  mp, "sec_1_3"),
            ("sec_2_1",  mp, "sec_2_1"),
            ("sec_2_2",  mp, "sec_2_2"),
            ("sec_3",    mp, "sec_3"),
            ("sec_4_1",  mp, "sec_4_1"),
            ("sec_4_2",  mp, "sec_4_2"),
            ("subsidy_project_name", sp, "subsidy_project_name"),
            ("subsidy_2_1", sp, "subsidy_2_1"),
            ("subsidy_2_2", sp, "subsidy_2_2"),
            ("subsidy_2_3", sp, "subsidy_2_3"),
            ("subsidy_4_1", sp, "subsidy_4_1"),
            ("subsidy_4_2", sp, "subsidy_4_2"),
        ]

        # 業務効率化セクション（Noneの場合はスキップ）
        if sp.has_efficiency and sp.subsidy_3_1 is not None:
            section_refs.append(("subsidy_3_1", sp, "subsidy_3_1"))
        if sp.has_efficiency and sp.subsidy_3_2 is not None:
            section_refs.append(("subsidy_3_2", sp, "subsidy_3_2"))

        for section_id, parent, field_name in section_refs:
            limit = self.CHAR_LIMITS.get(section_id)
            if limit is None:
                continue

            section_obj = getattr(parent, field_name, None)
            if section_obj is None:
                continue

            text = section_obj.text
            if not text:
                continue

            for attempt in range(self.MAX_CONDENSE_RETRIES):
                if len(text) <= limit:
                    break

                self.logger.info(
                    "[%s] %s が文字数超過 (%d/%d字)。圧縮試行 %d/%d",
                    self.agent_id,
                    section_id,
                    len(text),
                    limit,
                    attempt + 1,
                    self.MAX_CONDENSE_RETRIES,
                )

                condense_system = (
                    "あなたはテキスト圧縮の専門家です。\n"
                    "与えられたテキストを意味・要点を保ったまま指定文字数以内に圧縮してください。\n"
                    "圧縮後のテキストのみを出力してください。余計な説明は不要です。"
                )
                condense_user = (
                    f"以下のテキストを{limit}文字以内に圧縮してください。\n"
                    f"現在{len(text)}文字です。\n\n{text}"
                )
                text = await call_claude(
                    system_prompt=condense_system,
                    user_message=condense_user,
                    temperature=0.1,
                )
                text = text.strip().strip('"').strip("```").strip()

            # 圧縮結果を反映
            section_obj.text = text

        return sections

    # ------------------------------------------------------------------
    # 審査基準整合性チェック
    # ------------------------------------------------------------------

    async def _build_scoring_alignment(
        self, sections: StorySections, scoring_criteria: list
    ) -> list[ScoringAlignment]:
        """審査基準の各項目がストーリーのどのセクションで対応されているか評価する。"""
        if not scoring_criteria:
            return []

        mp = sections.management_plan
        sp = sections.subsidy_plan

        sections_text = {
            "sec_1_1": mp.sec_1_1.text,
            "sec_1_2": mp.sec_1_2.text,
            "sec_1_3": mp.sec_1_3.text,
            "sec_2_1": mp.sec_2_1.text,
            "sec_2_2": mp.sec_2_2.text,
            "sec_3": mp.sec_3.text,
            "sec_4_1": mp.sec_4_1.text,
            "sec_4_2": mp.sec_4_2.text,
            "subsidy_project_name": sp.subsidy_project_name.text,
            "subsidy_2_1": sp.subsidy_2_1.text,
            "subsidy_2_2": sp.subsidy_2_2.text,
            "subsidy_2_3": sp.subsidy_2_3.text,
            "subsidy_4_1": sp.subsidy_4_1.text,
            "subsidy_4_2": sp.subsidy_4_2.text,
        }

        if sp.has_efficiency and sp.subsidy_3_1 is not None:
            sections_text["subsidy_3_1"] = sp.subsidy_3_1.text
        if sp.has_efficiency and sp.subsidy_3_2 is not None:
            sections_text["subsidy_3_2"] = sp.subsidy_3_2.text

        system_prompt = (
            "あなたは補助金審査の専門家です。\n"
            "審査基準の各項目が、申請書ストーリーのどのセクションでカバーされているか評価してください。\n"
            "セクションIDは sec_1_1, sec_1_2, sec_1_3, sec_2_1, sec_2_2, sec_3, sec_4_1, sec_4_2,\n"
            "subsidy_project_name, subsidy_2_1, subsidy_2_2, subsidy_2_3, subsidy_3_1, subsidy_3_2,\n"
            "subsidy_4_1, subsidy_4_2 のいずれかを使用してください。\n"
            "JSON配列で回答してください:\n"
            '[{"criteria_item": "審査項目名", "addressed_in": "対応セクションID", "coverage": "full|partial|missing"}, ...]'
        )

        user_message = (
            f"## 審査基準\n{json.dumps(scoring_criteria, ensure_ascii=False, indent=2)}\n\n"
            f"## ストーリー各セクション\n{json.dumps(sections_text, ensure_ascii=False, indent=2)}"
        )

        raw_response = await call_claude(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
        )

        result = parse_json_response(raw_response)
        if isinstance(result, dict):
            result = result.get("alignments", result.get("items", []))
        if not isinstance(result, list):
            result = []

        alignments = []
        for item in result:
            if isinstance(item, dict):
                try:
                    alignments.append(ScoringAlignment(**item))
                except Exception as e:
                    self.logger.warning("ScoringAlignment parse error: %s / item=%s", e, item)

        return alignments

    # ------------------------------------------------------------------
    # 一貫性チェック
    # ------------------------------------------------------------------

    async def _check_consistency(
        self,
        sections: StorySections,
        expenses: dict,
        financial: dict,
    ) -> ConsistencyCheck:
        """ストーリーの一貫性を検証する。

        - 解決策セクション（subsidy_3_2）の expense_mapping が#7出力に存在するか
        - 現状（sec_1_2）で使った数値が#6決算書と整合するか
        """
        issues: list[str] = []
        sp = sections.subsidy_plan
        mp = sections.management_plan

        # 1. 業務効率化の経費項目整合性チェック
        expense_items_from_7: set[str] = set()
        if expenses:
            for item in expenses.get("items", []):
                name = item.get("item_name", "")
                if name:
                    expense_items_from_7.add(name)

        if sp.has_efficiency and sp.subsidy_3_2 is not None:
            for mapping in sp.subsidy_3_2.expense_mapping:
                if expense_items_from_7 and mapping.expense_item not in expense_items_from_7:
                    issues.append(
                        f"業務効率化の経費項目「{mapping.expense_item}」が"
                        "経費計算（#7）の出力に見つかりません"
                    )

        # 2. 決算書データの参照チェック（sec_1_2 の data_references）
        if financial and mp.sec_1_2.data_references:
            financial_text = json.dumps(financial, ensure_ascii=False)
            for ref in mp.sec_1_2.data_references:
                numbers = re.findall(r"[\d,]+", ref)
                for num in numbers:
                    clean_num = num.replace(",", "")
                    if len(clean_num) >= 3 and clean_num not in financial_text:
                        issues.append(
                            f"売上・利益セクションの参照「{ref}」の数値が"
                            "決算書データと一致しない可能性があります"
                        )

        # 3. 経営課題（sec_1_3）と補助事業目的（subsidy_2_2）の連動チェック
        if mp.sec_1_3.text and sp.subsidy_2_2.text:
            # key_pointsを使って課題キーワードが補助事業計画に含まれるか簡易チェック
            for kp in mp.sec_1_3.key_points:
                if len(kp) >= 4 and kp not in sp.subsidy_2_2.text and kp not in sp.subsidy_2_3.text:
                    issues.append(
                        f"経営課題のキーポイント「{kp}」が補助事業計画書（subsidy_2_2/2_3）に"
                        "明示的に言及されていない可能性があります"
                    )

        is_consistent = len(issues) == 0

        return ConsistencyCheck(
            is_consistent=is_consistent,
            issues=issues,
        )
