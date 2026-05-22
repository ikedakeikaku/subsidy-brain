# subsidy-brain

**日本の中小企業向け補助金申請を AI-native に書き直すエージェント。カンパニーブレインの第一弾。**

[![ci](https://github.com/ikedakeikaku/subsidy-brain/actions/workflows/demo.yml/badge.svg)](https://github.com/ikedakeikaku/subsidy-brain/actions)

> Singularity Society BootCamp（第4回）応募用プロトタイプ。

---

## 30秒で：何が動くのか

**補助金の名前を1行渡せば、人間コンサルと同じ動きで申請書を作る**：

```bash
uv run python demo/run_natural_demo.py "持続化補助金 第19回"
uv run python demo/run_natural_demo.py --live "ものづくり補助金 第18次"
uv run python demo/run_natural_demo.py --no-cache "省力化投資補助金 第2回"
```

事前に YAML を整備しなくてよい。エージェントが補助金ごとに：

1. **SubsidyDiscoverer** が公式サイトの URL と様式 URL を Web 検索で発見
2. **ProfileSynthesizer** が公募要領を読み込み、セクション構造・文字数・加点項目・必須グラフ/表を**その場で判断**して SubsidyProfile を合成
3. **profile_cache** が合成結果をディスクに保存（次回は瞬時に再利用）
4. **GuidelineFetcher** が公募要領 PDF と公式 Word/Excel 様式を自動ダウンロード＋キャッシュ
5. **AdoptionResearcher** が業種別の採択事例を Web 調査し、スキルストアに蓄積
6. **TemplateSynthesizer** が公式 docx をそのまま使う（取得できれば）／なければ profile から動的に生成
7. **StoryBuilder**（Claude）が事業者ヒアリング・決算データを統合してストーリーを構築
8. **document_assembler** が profile に従ってグラフ・テーブルを埋め込み
9. **adoption_estimator** が6軸で採択確率を採点
10. **refinement_loop** が目標未達なら最弱セクションを特定→Claudeで書き直し→再採点を最大Nイテレーション繰り返す
11. **xlsx_filler** が Excel 様式（経費明細書 等）をフォーマット保持で充填（drawings/罫線/conditional formatting を壊さない）
12. すべての実行を **skill_store** に記録し、フィードバック（採択／不採択）で次回の生成パターンに重み付け

```bash
git clone https://github.com/ikedakeikaku/subsidy-brain && cd subsidy-brain
uv sync --extra dev
uv run pytest -ra                              # 35 tests
uv run python demo/run_natural_demo.py "持続化補助金 第19回"  # 自然言語で1コマンド
ANTHROPIC_API_KEY=sk-ant-... \
  uv run python demo/run_natural_demo.py --live "ものづくり補助金 第18次"
```

### デモ出力サンプル

```
============================================================
 ✓ Full pipeline complete
   subsidy            : 販路開拓支援補助金（架空・デモ用）
   docx               : demo/output/full_pipeline_application.docx
   docx size          : 87,415 bytes
   sections           : 8
   chars              : 4,595 / 5,700 (100% compliance)
   charts inserted    : chart_revenue_trend, chart_effect_before_after
   tables inserted    : table_schedule, table_expense
   adoption probability: 90/100 (達成)
   refinement         : 90→90 over 1 iter(s)
   xlsx (経費明細)    : demo/output/経費明細書.xlsx (25 cells replaced, format preserved)
   manifest           : demo/output/full_pipeline_application.manifest.json
============================================================
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

## 公式テンプレートをそのまま使う

実 様式 docx には `{{placeholder}}` マーカーが無い（公的機関は普通の Word
として配布する）。subsidy-brain の `tools/official_form_filler.py` は、
それを**構造マッチで認識して**そのまま埋める：

| 埋め方 | 仕組み |
|---|---|
| **セクション本文** | 公募要領由来の見出し（"1-1. 自社の概要", "4-2. 今後のプラン"等）を section_number で照合、見出し直後の空段落に本文を書き込む |
| **加点項目** | 「重点政策加点」「事業環境変化」等を tag マッチで判定 |
| **申請者情報セル** | 「事業者名」「代表者氏名」「事業実施場所」「従業員数」等のラベル隣セルに自動充填 |
| **スケジュール表 / 経費明細表** | profile が宣言する table 群を末尾に追加 |
| **グラフ** | profile が宣言する chart 群を末尾に追加（PNG 埋め込み） |

公式の docx は罫線・page setup・defined names が全部保持されます。
`fill_method = "official_form_filler"` / `template_source = "official"` で
動作したことを manifest.json から確認できます。

公式 URL が `presets/<id>.yaml` または `agents/subsidy_discoverer.py` 経由
で得られれば、`GuidelineFetcher` が公式 .docx をダウンロード → そのまま
filler が使う。URL が無い場合は `templates/official_style_sample/` の
モック「公式風」 docx で代用してデモが完走します。

## 補助金ごとに最適化される仕組み

申請書のあるべき姿は補助金ごとに違う（持続化補助金は8〜10ページ、ものづくり補助金は10〜15ページ、事業再構築補助金は15〜20ページ）。subsidy-brain は **補助金ごとに Python コードを書くのではなく、profile YAML で宣言する** 方式を採用しています。

```yaml
# demo/sample_profile.yaml
sections:
  - section_id: section_1_1
    display_name: 1-1. 自社の概要
    target_chars: 600       # 目標
    min_chars: 450          # これを下回ると審査で不利
    max_chars: 800          # 公募要領上限
charts:
  - chart_id: chart_revenue_trend
    chart_type: revenue_trend
    place_after_section: section_1_2
tables:
  - table_id: table_schedule
    table_type: schedule
    place_after_section: section_4_2
```

1つの汎用エンジンが**全補助金で動く**：

| エンジン | 役割 |
|---|---|
| `tools/length_validator.py` | profile の min/target/max に対する compliance 検証 |
| `tools/document_assembler.py` | profile に従ってセクション・グラフ・表を順番に組み立て |
| `tools/quality_scoring.py` | 4軸（長さ／具体数値／構造／視覚的資産）で自己採点 |

新しい補助金を追加するときは、Python コードを1行も書かず、`profile.yaml` を1つ追加するだけ。

### フォーマット保持（Word／Excel）

申請書を**スクラッチでビルドしない**。テンプレートを開いて placeholder を
置換するだけ、という設計で罫線・罫線・スタイル・名前付き範囲を死守します。

**Word（.docx）：** `tools/template_filler.py`
- `{{placeholder}}` 置換式
- 罫線・Heading 連番・余白・既定フォントはテンプレ側が保持
- python-docx で再構築されるのは置換後の本文だけ

**Excel（.xlsx）：** `tools/xlsx_filler.py`
- **openpyxl を round-trip に使わない**（drawings・斜線・conditional formatting を壊す）
- `zipfile` で .xlsx を ZIP として開き、`xl/sharedStrings.xml` と
  `xl/worksheets/sheet*.xml` のテキスト部分**だけ**を置換
- それ以外（`xl/styles.xml`・`xl/drawings/`・`xl/theme/`・`xl/_rels/`等）は**バイト同一**でコピー
- 結果として drawings／conditional formatting／data validation／defined names が全て生き残る
- テスト `test_xlsx_filler_does_not_touch_non_text_parts` で byte-identical を検証

### 自己改善ループ（目標スコア到達まで自動で書き直し）

初回ドラフトが採択確率の目標を下回った場合、`tools/refinement_loop.py` が
自動で次を繰り返します：

1. 6軸の `adoption_estimator` で採点
2. もっとも回復幅が大きい弱点セクションを特定
3. そのセクションを Claude で書き直し（live モード）またはルール駆動でパッチ（mock モード）
4. 再採点 → 目標達成まで（最大 N 回、既改善セクションはスキップ）

```text
BEFORE: 29/100  (weak: section_1_1, section_4_2, section_1_2, ...)
  iter 0: 29 → 52   refined: section_1_1   reason: 自社固有データ; 課題→施策対応
  iter 1: 52 → 56   refined: section_4_2   reason: 課題→施策対応
  iter 2: 56 → 58   refined: section_3     reason: 自社固有データ
  ...
AFTER:  66/100  (passed=True)
```

リファインメント履歴は `manifest.json` に保存され、スキルストアに学習対象として記録されます。

### `presets/` は任意（オフラインCI例 / オーバーライド用）

`presets/` 配下の YAML は**もう必須ではない**。エージェントが補助金ごとに
profile を合成するため、新しい補助金に対応するために事前準備は不要。

何のために残しているか：

- **オフライン CI 例** — `ANTHROPIC_API_KEY` / `PERPLEXITY_API_KEY` が無い
  環境で synthesizer がフォールバックする際の挙動を確認するため
- **スキーマ例** — `jizoku_19_profile.yaml` を見れば synthesizer が出力
  する形が分かる
- **手動オーバーライド** — 自動合成された profile が気に入らないときに、
  `presets/<id>_profile.yaml` に手書きで上書き保存すると、`profile_cache`
  より優先して使われる

詳細は [`presets/README.md`](presets/README.md)。

### Web 検索の統一プロバイダ

`tools/web_search.py` は **Anthropic Claude の built-in web_search ツール**
を優先的に使い、Perplexity をフォールバックとして使う統一インタフェース。

| プロバイダ | 優先 | 用途 |
|---|---|---|
| Anthropic (`web_search_20250305`) | 第1 | 補助金URLの発見・採択事例調査 |
| Perplexity (Sonar) | フォールバック | Anthropic web_search が利用できない環境向け |
| none | 無キー時 | CI / 公開デモ向け safe no-op |

`AdoptionResearcher` と `SubsidyDiscoverer` の両方が同じ抽象を使うので、
プロバイダの切替はキー設定だけで完結します。

### 運用観点（コスト・観測性）

- **コスト追跡** `tools/cost_tracker.py`：全 Claude 呼び出しの token 消費を
  agent 別に集計し、USD/JPY で表示。manifest.json に記録されるので、
  どのエージェントが何円使ったかが事後検証できる
- **構造化ログ** `tools/observability.py`：`configure_logging()` 1行で JSON
  ログに切替。`run_id` で全エージェントのトレースが追える

### グラフ・表の自動生成

| 種別 | 自動生成例 | データソース |
|---|---|---|
| グラフ | 月次/年次売上推移（棒） | `financial.past_3y_pl` |
| グラフ | 補助事業 before/after | `planned_project.expected_outcomes` |
| 表 | P/L 履歴 | `financial.past_3y_pl` |
| 表 | スケジュール（採択〜実績報告〜自走） | `planned_project.schedule` |
| 表 | 経費明細 | `expenses.breakdown` |

matplotlib で PNG 生成 → python-docx の `add_picture()` で埋め込み。日本語フォントは macOS / Linux 両方を自動判別。

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
