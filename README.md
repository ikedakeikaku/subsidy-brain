# subsidy-brain

**日本の中小企業向け補助金申請を、人間コンサルと同じ動きで生成する AI-native エージェント。事前に YAML を整備しない。エージェントが補助金ごとに公募要領を調査し、構造を判断し、申請書を組み立て、採択確率が目標に届くまで自己改善する。**

[![ci](https://github.com/ikedakeikaku/subsidy-brain/actions/workflows/demo.yml/badge.svg)](https://github.com/ikedakeikaku/subsidy-brain/actions)

> Singularity Society BootCamp（第4回）応募用プロトタイプ。

---

## 30秒で：何が動くのか

**補助金の名前を1行渡せば、申請書ドラフトが .docx で出る**：

```bash
git clone https://github.com/ikedakeikaku/subsidy-brain && cd subsidy-brain
uv sync --extra dev
uv run pytest -ra                                              # 17 tests
uv run python demo/run_natural_demo.py "持続化補助金 第19回"
ANTHROPIC_API_KEY=sk-ant-... \
  uv run python demo/run_natural_demo.py --live "ものづくり補助金 第18次"
```

エージェントが補助金ごとに **その場で判断** する：

1. **SubsidyDiscoverer** — Anthropic Claude の built-in web_search で
   公式サイトの URL・様式 URL を発見
2. **ProfileSynthesizer** — 公募要領を読み取り、セクション構造・
   文字数上限・必須グラフ／表・加点項目を構造化（不明な数値は申請書
   実務として妥当な値を推定）
3. **profile_cache** — 合成結果をローカル FS に保存、次回は瞬時に再利用
4. **GuidelineFetcher** — 公募要領 PDF と公式 様式 docx/xlsx を自動 DL
5. **AdoptionResearcher** — 業種別の採択事例を Web 調査、skill_store に蓄積
6. **TemplateSynthesizer** — 公式 docx が取得できればそのまま使い、
   なければ "DRAFT" 警告付きスケルトンを合成
7. **StoryBuilder** — 事業者プロファイル＋公募要領＋採択ナレッジを統合
   して Claude tool_use で構造化ストーリーを生成
8. **OfficialFormFiller / document_assembler** — 公式 docx ならば
   フォーマット保持で挿入、合成テンプレならば profile に従い組み立て
9. **adoption_estimator** — 6軸で採択確率を採点
10. **refinement_loop** — 目標未達なら最弱セクションを特定→Claude で
    書き直し→再採点を最大 N イテレーション繰り返す
11. **xlsx_filler** — Excel 様式は drawings/罫線/conditional formatting
    を壊さず zipfile 直接編集で充填
12. **skill_store** — フィードバック（採択/不採択）で次回の生成パターン
    に重み付け

```
============================================================
 ✓ Natural-language pipeline complete
   query              : '持続化補助金 第19回'
   resolved subsidy   : 持続化補助金 第19回
   profile source     : synthesized
   docx               : demo/output/持続化補助金_第19回_application.docx
   docx size          : 85,830 bytes
   sections           : 8
   chars              : 4,595 / 5,700 (100% compliance)
   charts inserted    : chart_revenue_trend, chart_effect_before_after
   tables inserted    : table_schedule, table_expense
   fill method        : official_form_filler / document_assembler
   template source    : official / draft
   adoption probability: 90/100 (達成)
============================================================
```

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

## 「事前データを持たない」設計

`presets/` と `templates/` は**空**である。これは意図的。

以前のバージョンでは `presets/jizoku_19_profile.yaml` のような「ハードコードされた補助金構造」を同梱していたが、これは：

- 文字数・必須項目・URL の値はすべて**推定**（公募要領に明記が無い数字を含む）
- 公式と異なる可能性が高い
- 「システムが補助金を知っている」という誤解を生む

正しい設計は **人間コンサルと同じ**：

> 新しい補助金が来たら、公募要領を読み、様式 docx をダウンロードし、
> 採択事例を調べ、その上で申請書の構造を判断する。

これを `ProfileSynthesizer` と `SubsidyDiscoverer` と `GuidelineFetcher` で
やる。事前に YAML を整備する必要は無い。

### 公式 docx の使い方

`SubsidyDiscoverer` が正しい URL を見つけて `GuidelineFetcher` が
DL に成功すれば、**公式 docx は OfficialFormFiller によってそのまま埋められる**。
罫線・page setup・defined names は完全保持される。

URL が得られない／DL に失敗した場合は、`TemplateSynthesizer` が
「**DRAFT — 本ファイルは公式 様式 ではありません**」と明示した
スケルトンを合成する。これを公式と誤認することは無い。

### 上書きしたいとき（オプション）

profile の自動合成結果が気に入らない場合は、
`presets/<program_id>_profile.yaml` を自分で書いて置く。
パイプラインはこれをキャッシュより優先する。詳細は
[`presets/README.md`](presets/README.md)。

---

## アーキテクチャ

```
[ユーザー入力]  補助金の名前（自然言語）＋事業者プロファイル（YAML）
       │
       ▼
SubsidyDiscoverer  ── Anthropic web_search →  公式URL・様式URL
       │
       ▼
ProfileSynthesizer ── 公募要領を読む    →  SubsidyProfile（合成）
       │                                       ↓
       ▼                                  profile_cache
GuidelineFetcher   ── 公式PDF/docx DL  →  fetched_form_paths
       │
       ▼
AdoptionResearcher ── 採択事例調査     →  skill_store.knowledge
       │
       ▼
StoryBuilder (Claude tool_use)        →  ApplicationStory
       │
       ▼
adoption_estimator ── 6軸採点          →  score
       │
       ▼   未達なら ↓
refinement_loop    ── 弱点セクション再生成 → loop
       │
       ▼   達成
       │
       ▼
[公式docxが取れた場合] OfficialFormFiller — 見出しマッチで本文挿入
[取れない場合]         document_assembler — profile から組み立て
       │
       ▼
[出力]  申請書.docx ＋ 経費明細.xlsx（任意）＋ manifest.json
       │
       ▼
   ExecutionLog → skill_store（フィードバックで版管理スコア更新）
```

### 主要な設計原則

**① ビジネスロジック付きAPIが本体**：公募要領の不変条件（経費区分・
審査基準・文字数）を Pydantic で型化。LLM も UI も同じ API を呼ぶ
対等な一級クライアント。

**② 公式テンプレートのフォーマット保持**：python-docx でスクラッチビルド
しない。公式 様式.docx を開いて見出しマッチで本文を挿入する。
罫線・Heading連番・余白・既定フォント・defined names はテンプレ側が保持。

**③ Excel フォーマット保持**：`tools/xlsx_filler.py` は openpyxl を
round-trip に使わない。zipfile で .xlsx を ZIP として開き、
`xl/sharedStrings.xml` と `xl/worksheets/sheet*.xml` のテキスト部分
だけを置換。drawings/conditional formatting/defined names が
バイト同一で生き残る（テスト `test_xlsx_filler_does_not_touch_non_text_parts` で検証）。

**④ Claude API 2026年ベストプラクティス**：
- `tool_use` で必ずスキーマ準拠 JSON
- `cache_control: ephemeral` で長いシステムプロンプトを 90% コスト削減
- Built-in `web_search_20250305` ツール（旧 Perplexity 経路は fallback）
- Lazy client init で API key 無くても import 可

**⑤ ローカルファイルシステムを DB として使う**：skill_store は JSONL。
人間も他のエージェントも同じファイルを直接読める。

---

## 自己改善ループ（採択確率を目標まで自動で上げる）

初回ドラフトが目標未達なら、`refinement_loop` が：

1. 6軸の `adoption_estimator` で採点
2. もっとも回復幅が大きい弱点セクションを特定
3. そのセクションを Claude で書き直し（live）／決定論的にパッチ（mock）
4. 再採点 → 達成まで（最大 N 回、既改善セクションはスキップ）

```text
BEFORE: 29/100  (weak: section_1_1, section_4_2, section_1_2, ...)
  iter 0: 29 → 52   refined: section_1_1   reason: 自社固有データ; 課題→施策対応
  iter 1: 52 → 56   refined: section_4_2   reason: 課題→施策対応
  iter 2: 56 → 58   refined: section_3     reason: 自社固有データ
  ...
AFTER:  66/100  (passed=True)
```

リファインメント履歴は `manifest.json` に保存され、skill_store に学習対象として記録される。

---

## どんどん賢くなる仕組み（skill_store）

`tools/skill_store.py` がローカル FS の `.skill_store/` に JSONL で蓄積：

| 蓄積物 | 中身 | スコア更新 |
|---|---|---|
| `ExecutionLog` | 全エージェント実行ログ（入出力ハッシュ・所要時間・使ったスキル） | — |
| `SkillEntry` | 抽出された know-how（プロンプトパターン・業種別例・採択ルーブリック） | フィードバックで±0.10 |
| `FeedbackInput` | 採択／不採択結果＋審査員コメント | スコア更新の駆動 |
| `knowledge/*.json` | 業種別・補助金別の自由形式ナレッジ（AdoptionResearcher が書き込む） | 上書き |

採択フィードバックが入るたび、該当の skill のスコアが上がり、次回の検索で優先される（テスト `test_skill_store_learns_from_feedback` で 0.60→0.70→0.60 の上下動作が検証済み）。

---

## 構成要素

```
subsidy-brain/
├── agents/
│   ├── orchestrator.py            # #4 全体統括
│   ├── profile_synthesizer.py     # 補助金名→SubsidyProfile（Web調査経由）
│   ├── template_synthesizer.py    # 公式docx取得 or DRAFT合成
│   ├── subsidy_discoverer.py      # 補助金名→URL/様式URLをWeb発見
│   ├── adoption_researcher.py     # 採択事例のWeb調査→skill_store蓄積
│   ├── guideline_fetcher.py       # 公募要領・様式の自動DL＋キャッシュ
│   ├── guideline_parser.py        # #5 公募要領PDF→不変条件JSON
│   ├── financial_reader.py        # #6 決算書読込
│   ├── expense_calc.py            # #7 経費計算
│   ├── story_builder.py           # #8 ストーリー構築（Claude）
│   ├── fact_checker.py            # #12 統計データ裏付け検証
│   ├── quality_check.py           # #14 採択パターン照合・自己採点
│   └── document_builder.py        # #13 公式テンプレ流し込み or 組立
├── tools/
│   ├── web_search.py              # Anthropic優先 / Perplexity fallback の統一層
│   ├── claude_client.py           # Claude API（prompt caching・tool_use・usage tracking）
│   ├── profile_cache.py           # SubsidyProfileのディスクキャッシュ
│   ├── skill_store.py             # 自己改善層（JSONL）
│   ├── document_assembler.py      # profile駆動の組立（DRAFT用）
│   ├── official_form_filler.py    # 公式docxを見出しマッチで埋める
│   ├── template_filler.py         # {{placeholder}}置換式（合成テンプレ向け）
│   ├── xlsx_filler.py             # フォーマット保持xlsx充填
│   ├── refinement_loop.py         # 自己改善ループ
│   ├── adoption_estimator.py      # 6軸採択確率推定
│   ├── length_validator.py        # min/target/max compliance
│   ├── quality_scoring.py         # 補助的4軸スコア
│   ├── cost_tracker.py            # Claude使用token/料金集計
│   ├── observability.py           # structlogベース構造化ログ
│   ├── chart_tools.py             # matplotlib図表生成
│   └── perplexity_search.py
├── schemas/                       # 15個のPydanticスキーマ + Protocol宣言
│   ├── subsidy_profile.py         # SubsidyProfile / SectionSpec / ChartSpec / TableSpec
│   ├── subsidy_registry.py        # SubsidyProgram / SubsidyForm
│   └── integrations.py            # Phase 2/3 の Protocol
├── config/
├── demo/
│   ├── run_natural_demo.py        # 唯一の正規エントリー（NL→docx）
│   ├── story_builder_live.py      # Claude tool_use ヘルパー
│   ├── mock_story.py              # オフライン用 deterministic mock
│   └── sample_company.yaml        # 架空applicant company data
├── presets/                       # 空（オプショナルの上書き層）
├── templates/                     # 空（runtime生成 or 公式docx置き場）
└── tests/                         # 17 tests
```

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
| 1（このリポ） | 完了 | 補助金申請の一気通貫生成（自然言語→docx＋xlsx＋manifest） |
| 2 | 〜7ヶ月 | CFOビュー：月次P/L監視 × 資金繰り予測 × 補助金マッチング |
| 3 | 〜12ヶ月 | データ統合層：freee / Drive / Gmail / LINE を MCP 経由でローカル FS に正規化 |
| 4（長期） | — | カンパニーブレイン：全社データを横断する経営判断エージェント |

詳細：[`docs/roadmap.md`](docs/roadmap.md) / [`docs/vision.md`](docs/vision.md) / [`docs/architecture.md`](docs/architecture.md)

---

## 開発体制とライセンス

- 開発：池田計画合同会社・池田哲郎、Claude Code とのステップバイステップ協働開発
- BootCamp期間中の到達点：本リポ + Phase 2 CFOビュー + Phase 3 データ統合層の最小一体化
- ライセンス：MIT
