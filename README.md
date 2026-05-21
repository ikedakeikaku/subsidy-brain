# subsidy-brain

**日本の中小企業向け補助金申請を AI-native に書き直すエージェント。カンパニーブレインの第一弾。**

[![ci](https://github.com/ikedakeikaku/subsidy-brain/actions/workflows/demo.yml/badge.svg)](https://github.com/ikedakeikaku/subsidy-brain/actions)

> Singularity Society BootCamp（第4回）応募用プロトタイプ。

---

## 30秒で：何が動くのか

補助金IDを1つ渡すと、以下が**全自動**で走ります：

1. 補助金マスタを引いて公募要領 PDF と公式 Word 様式を**自動ダウンロード＋キャッシュ**
2. Web で**採択事例を自動調査**し、業種別ナレッジとしてスキルストアに蓄積
3. 公募要領の不変条件（経費区分・審査基準・文字数上限）を構造化
4. 事業者ヒアリング・決算データと統合して**ストーリーを Claude で構築**
5. 公式 Word 様式に**そのまま流し込み**（罫線・スタイル・フォントは壊れない）
6. ファクトチェック・品質チェック・自己採点
7. すべての実行を学習層に記録し、**次の案件で活用**

```bash
git clone https://github.com/ikedakeikaku/subsidy-brain && cd subsidy-brain
uv sync --extra dev
uv run pytest -ra                              # 11 tests
uv run python demo/run_full_demo.py            # 一気通貫デモ（offline mock）
ANTHROPIC_API_KEY=sk-ant-... \
  uv run python demo/run_full_demo.py --live   # 実 Claude 呼び出し
```

CI でも同じパイプラインが毎 push 走り、生成 Word とマニフェストは `Actions` タブから artifact として取れます。

---

## なぜ作るのか

日本の小規模事業者・中小企業向け補助金は年間数千億円規模が用意されている。にもかかわらず、現場ではしばしば**補助金そのものが事業者の足を引っ張る**。

- 申請代行の成功報酬が補助金の 10〜20% を持っていく
- 不採択でも着手金は戻らない
- 採択後の実績報告・経費精算が重く、本業が止まる
- そもそも該当する補助金を知らずに機会損失している

人月課金のコンサル業界が中抜きしている典型市場。AI-native に作り直して**価格を一桁以上下げる余地がある**領域。

私（[池田計画合同会社](https://ikedakeikaku.jp) / 池田哲郎）は補助金申請コンサル現役で、毎日この無駄を見ている。エンジニア視点だけでは「中小企業データの汚さ」が見えず、コンサル視点だけでは「AI-native API中心アーキテクチャ」に到達しない。この交点で作る。

---

## パイプライン全体図

```
┌─── ユーザー入力: 補助金ID（例: "sample_hanro_kaitaku_v1"）+ 事業者YAML
│
├─[a] SubsidyRegistry          補助金マスタを引く（ID→URL→様式パス）
│
├─[b] GuidelineFetcher          ┌→ 公募要領PDFを自動ダウンロード
│                               └→ 公式 様式1/様式2 .docx を自動ダウンロード＋ローカルキャッシュ
│
├─[c] AdoptionResearcher       Perplexity で「{補助金名} 採択事例 {業種}」を Web 調査
│                              → skill_store に蓄積（次回案件で参照）
│
├─[d] GuidelineParser    #5     公募要領PDF → 不変条件 JSON（経費区分・審査基準・文字数）
├─[e] FinancialReader    #6     決算書 → P/L・B/S サマリー
├─[f] ExpenseCalc        #7     見積書 → 経費明細＋区分照合
│        ↓
├─[g] StoryBuilder       #8     全部を統合 → ApplicationStory（Claude tool_use）
│        ↓
├─[h] FactChecker       #12     統計データの裏付け検証（Perplexity）
├─[i] QualityChecker    #14     採択パターン照合・自己採点
│        ↓
├─[j] DocumentBuilder   #13     公式 様式 .docx を**テンプレ流し込み**で生成
│                               → 罫線・スタイル・フォントは公式が保持
│
└─→ 出力: 申請書 .docx + manifest.json
            ↓
        Execution Log → skill_store（フィードバックで版管理スコア更新）
```

オーケストレーター（`agents/orchestrator.py`）が依存関係とリトライを管理。

---

## 「フォーマットが壊れない」設計

申請書を **スクラッチでビルドしない**。公式の `様式X.docx` を**テンプレートとして読み込み**、`{{section_1_1}}` のような placeholder を内容に置換するだけ。

- 表の罫線スタイル → テンプレ側が保持
- セクション番号の Heading 連番 → テンプレ側が保持
- 余白・既定フォント → テンプレ側が保持
- python-docx で再構築されるのは「置換後の本文だけ」

`tools/template_filler.py` がこの責務を持つ。デモでは
`templates/build_sample_template.py` が架空補助金の様式2を生成し、
テンプレ流し込みでデモ申請書を作る（22 placeholder すべて置換、missing 0、テストで動作確認済み）。

実運用では公式の `様式2.docx` を `templates/<program_id>/` に置けば、即同じ仕組みで動く。

---

## どんどん賢くなる仕組み

`tools/skill_store.py` がローカルファイルシステムに JSONL で蓄積：

| 蓄積物 | 中身 | スコア更新 |
|---|---|---|
| `ExecutionLog` | 全エージェント実行ログ（入出力ハッシュ・所要時間・使ったスキル） | — |
| `SkillEntry` | 抽出された know-how（プロンプトパターン・業種別例・採択ルーブリック） | フィードバックで±0.10 |
| `FeedbackInput` | 採択／不採択結果＋審査員コメント | スコア更新の駆動 |
| `knowledge/*.json` | 業種別・補助金別の自由形式ナレッジ（AdoptionResearcher が書き込む） | 上書き |

採択フィードバックが入るたび、該当の skill のスコアが上がり、次回の検索で優先される。テスト `test_skill_store_learns_from_feedback` で 0.60→0.70→0.60 の上下動作が検証済み。

ローカルファイルシステムを DB として使う方式：opaque な DB を介さないので、人間も他のエージェントも同じファイルを読める。

---

## カンパニーブレインへの拡張

このリポは Phase 1。Phase 2/3 の seam は `schemas/integrations.py` に Protocol として既に宣言済み。

```python
class CFOLedgerReader(Protocol):
    async def fetch_monthly_pl(self, company_id: str, *, months: int = 24) -> list[MonthlyPL]: ...

class SubsidyMatcher(Protocol):
    async def match(self, profile: dict, *, limit: int = 10) -> list[SubsidyMatch]: ...

class DataConnector(Protocol):
    source_id: str
    async def sync(self, *, full: bool = False) -> DataSourceMetadata: ...
```

| Phase | 期間 | 中身 |
|---|---|---|
| 1（このリポ） | 完了 | 補助金申請の一気通貫生成（補助金ID→自動取得→Web調査→ストーリー→公式テンプレ流し込み） |
| 2 | 〜7ヶ月 | CFOビュー：月次P/L監視 × 資金繰り予測 × 補助金マッチング |
| 3 | 〜12ヶ月 | データ統合層：freee / Drive / Gmail / LINE を MCP 経由でローカル FS に正規化 |
| 4（長期） | — | カンパニーブレイン：全社データを横断する経営判断エージェント |

詳細：[`docs/roadmap.md`](docs/roadmap.md) / [`docs/vision.md`](docs/vision.md) / [`docs/architecture.md`](docs/architecture.md)

---

## 構成要素

```
subsidy-brain/
├── agents/
│   ├── orchestrator.py            # #4 全体統括
│   ├── guideline_fetcher.py       # 補助金マスタ→公募要領/様式の自動取得＋キャッシュ
│   ├── adoption_researcher.py     # 採択事例のWeb調査→skill_store蓄積
│   ├── guideline_parser.py        # #5 公募要領PDF→不変条件JSON
│   ├── financial_reader.py        # #6 決算書読込
│   ├── expense_calc.py            # #7 経費計算
│   ├── story_builder.py           # #8 ストーリー構築（Claude）
│   ├── fact_checker.py            # #12 統計データ裏付け検証
│   ├── quality_check.py           # #14 採択パターン照合・自己採点
│   └── document_builder.py        # #13 公式テンプレ流し込み→.docx
├── tools/
│   ├── claude_client.py           # Claude API（prompt caching・tool_use・usage tracking）
│   ├── skill_store.py             # 自己改善層（JSONL）
│   ├── template_filler.py         # placeholder 置換式 Word 生成
│   ├── perplexity_search.py
│   ├── pdf_tools.py / ocr_tools.py / docx_tools.py / chart_tools.py
│   └── bonus_points.py            # 加点判定
├── schemas/                       # 15個のPydanticスキーマ + Protocol宣言
│   ├── subsidy_registry.py        # SubsidyProgram / SubsidyForm / SubsidyRegistry
│   └── integrations.py            # Phase 2/3 の Protocol（CFO・DataConnector・…）
├── config/                        # settings (env-driven) / logging
├── demo/
│   ├── sample_company.yaml        # 架空案件
│   ├── sample_registry.yaml       # 架空補助金マスタ
│   ├── sample_guideline.md        # 架空公募要領
│   ├── run_demo.py                # 簡易版（StoryBuilder + テンプレ流し込み）
│   └── run_full_demo.py           # 一気通貫版（Registry→Fetcher→Researcher→...→Doc）
├── templates/
│   └── build_sample_template.py   # サンプル様式2.docxを生成（git diff-friendly）
├── tests/                         # 11 tests（smoke + full pipeline）
└── .github/workflows/             # CI（lint + test + 全デモ + artifact）
```

---

## このリポは「薄いコア」

実案件運用版は別途プライベートで稼働中（16エージェント、自己改善層、実採択ナレッジ、freee/Drive/LINE WORKS統合）。本リポはそのうち**プロトタイプ要件を満たす最小集合**を公開しています。

| 公開（このリポ） | プライベート |
|---|---|
| 9体のエージェント（オーケストレーター + 7コア + GuidelineFetcher + AdoptionResearcher） | + 自己改善・案件管理・LINE Bot |
| skill_store（JSONLバックエンド、フィードバック→スコア更新） | + 実案件採択パターン1,200件 |
| 架空補助金で end-to-end 動作（auto-fetch → research → 生成 → テンプレ流し込み） | + 持続化補助金等の実 .docx 様式 |
| Phase 2/3 Protocol宣言のみ | + freee/Drive/Gmail/LINE WORKS 本番実装 |

---

## 開発体制とライセンス

- 開発：池田計画合同会社・池田哲郎、Claude Code とのステップバイステップ協働開発（仕様策定とコードレビューは人間が担当、実装は AI が担当）
- BootCamp期間中の到達点：本リポ + Phase 2 CFOビュー + Phase 3 データ統合層の最小一体化
- ライセンス：MIT
