# subsidy-brain

**日本の中小企業向け補助金申請を AI-native に書き直すエージェント。カンパニーブレインの第一弾。**

[![ci](https://github.com/ikedakeikaku/subsidy-brain/actions/workflows/demo.yml/badge.svg)](https://github.com/ikedakeikaku/subsidy-brain/actions)

> Singularity Society BootCamp（第4回）応募用プロトタイプ／[募集概要](https://note.com/singsoc/n/nf3851d247ad7)

---

## 30秒で：何が・なぜ動くのか

- **入力**：公募要領（PDF / Markdown）＋ 事業者ヒアリング（YAML）＋ 決算データ
- **出力**：申請書ドラフト（`.docx`）＋ ストーリーJSON
- **中身**：オーケストレーター ＋ 7体の専門エージェント／Pydanticスキーマ駆動／Claude `tool_use` で構造化出力
- **学習**：使うほど賢くなる skill_store（採択・不採択フィードバックでスコア自動更新）

```bash
git clone https://github.com/ikedakeikaku/subsidy-brain && cd subsidy-brain
uv sync --extra dev
uv run pytest -ra                          # 5 smoke tests
uv run python demo/run_demo.py             # オフラインmockで .docx を生成
ANTHROPIC_API_KEY=sk-ant-... uv run python demo/run_demo.py --live  # 実Claude呼び出し
```

CI でも同じデモが毎push走り、生成 Word は `Actions` タブから artifact として取得できます。

---

## なぜ作るのか

日本の小規模事業者・中小企業向け補助金は年間数千億円規模が用意されている。にもかかわらず、現場ではしばしば**補助金そのものが事業者の足を引っ張る**。

- 申請代行の成功報酬が補助金の 10〜20% を持っていく
- 不採択でも着手金は戻らない
- 採択後の実績報告・経費精算が重く、本業が止まる
- そもそも該当する補助金を知らずに機会損失している

中島聡氏のいう「人月で稼ぐITコンサルの中抜き市場」（2025-05-27号）の典型例。AI-native に作り直して**価格を一桁以上下げる余地がある**領域。

私（[池田計画合同会社](https://ikedakeikaku.jp) / 池田哲郎）は補助金申請コンサル現役で、毎日この無駄を見ている。エンジニア視点だけでは「中小企業データの汚さ」が見えず、コンサル視点だけでは「AI-native API中心アーキテクチャ」に到達しない。この交点で作る。

---

## アーキテクチャ

```
                  ┌─ #5 公募要領パーサー  ─┐
[Orchestrator] ──┼─ #6 決算データ読込   ─┼→ #8 ストーリー構築 ─┐
   #4            └─ #7 経費計算         ─┘                   ├→ #13 Word組立 → #14 品質チェック → ✓
                                                              └─ #12 ファクトチェック ┘
                                                                ↑              ↑
                                                                Perplexity     skill_store (学習層)
```

- **ビジネスロジック付き API が本体**：公募要領の不変条件（経費区分・審査基準・加点要件・文字数上限）を Pydantic スキーマで型化し、ドメインモジュール側に固める
- **LLM と UI を対等な一級クライアントとして扱う**：同じ API を LLM 経由でも CLI 経由でも呼べる
- **Claude `tool_use` による構造化出力**：必ずスキーマ準拠の JSON が返る
- **プロンプトキャッシュ対応**：長い system prompt は `cache_control: ephemeral` で 90% コスト削減

設計思想は中島聡氏の「AI-native ビジネスアプリ・アーキテクチャ」（2026-05-19号）に従っています。詳しくは [`docs/architecture.md`](docs/architecture.md)。

---

## どんどん賢くなる仕組み

`tools/skill_store.py` がローカルファイルシステム上に JSONL で蓄積：

| 蓄積物 | 中身 | スコア更新 |
|---|---|---|
| `ExecutionLog` | 全エージェント実行ログ（入力/出力ハッシュ・所要時間・使ったスキル） | — |
| `SkillEntry` | 抽出された know-how（プロンプトパターン・業種別例・採択ルーブリック） | フィードバックで±0.10 |
| `FeedbackInput` | 採択／不採択結果＋審査員コメント | スコア更新の駆動 |
| `knowledge/*.json` | 業種別・補助金別の自由形式ナレッジ | 上書き保存 |

採択フィードバックが入るたび、該当の skill のスコアが上がり、次回の検索で優先される（テスト `test_skill_store_learns_from_feedback` で動作確認済み）。

中島聡氏の言う「ファイルシステムを DB として使う」（2026-04-07号）方式：opaque な DB を介さないので、人間も他のエージェントも同じファイルを読める。

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
| 1（このリポ） | 完了 | 補助金申請ドラフトの一気通貫生成 |
| 2 | 〜7ヶ月 | CFOビュー：月次P/L監視 × 資金繰り予測 × 補助金マッチング |
| 3 | 〜12ヶ月 | データ統合層：freee / Drive / Gmail / LINE を MCP 経由でローカル FS に正規化 |
| 4（長期） | — | カンパニーブレイン：全社データを横断する経営判断エージェント |

詳細：[`docs/roadmap.md`](docs/roadmap.md) / [`docs/vision.md`](docs/vision.md)

---

## このリポは「薄いコア」

実案件運用版は別途プライベートで稼働中（16エージェント、自己改善層、実採択ナレッジ、freee/Drive/LINE WORKS統合）。本リポはそのうち**プロトタイプ要件を満たす最小集合**を公開しています。

| 公開（このリポ） | プライベート |
|---|---|
| 7体のコアエージェント＋オーケストレーター | + 9体（自己改善・案件管理・LINE Bot・採択ナレッジ蓄積） |
| skill_store（最小実装＋テスト済み） | + 実案件採択パターン1,200件 |
| 架空案件デモ＋CI | + 実案件パイプライン（守秘義務） |
| Phase 2/3 Protocol宣言のみ | + freee/Drive/Gmail/LINE WORKS実装 |

---

## 構成要素

```
subsidy-brain/
├── agents/        オーケストレーター + 7体のコアエージェント
├── tools/         claude_client / skill_store / docx / pdf / ocr / chart / perplexity
├── schemas/       Pydantic スキーマ（13個 + integrations.py の Protocol）
├── config/        settings (env-driven) / logging
├── demo/          架空案件 + end-to-end runner
├── docs/          vision / roadmap / architecture
├── tests/         smoke tests（5件、全部 green）
└── .github/workflows/   CI（lint + test + demo artifact）
```

---

## 開発体制と進め方

- 開発：池田計画合同会社・池田哲郎、Claude Code とのペアプログラミング（中島氏 2025-09-02号「ステップバイステップで指示してコードレビュー」方式）
- BootCamp期間中の到達点：本リポ + Phase 2 CFOビュー + Phase 3 データ統合層の最小一体化
- ライセンス：MIT
