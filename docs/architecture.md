# Architecture

## 設計原則

ビジネスロジックを API として実装し、LLM と UI をどちらもその API を呼ぶ
対等な一級クライアントとして扱う。これにより、LLM が生成した申請書と UI
が編集した申請書の間に構造的なズレが生まれない。

```
            ┌─────────────────────────────┐
            │  Business Logic API (本体)    │
            │  - 公募要領の不変条件          │
            │  - 経費区分ルール              │
            │  - 加点要件・文字数上限        │
            │  - Pydanticスキーマで全て型付け │
            └─────────────────────────────┘
              ↑                       ↑
              │                       │
       ┌──────┴──────┐          ┌─────┴──────┐
       │  LLM Client  │          │  UI Client  │
       │  (Claude)    │          │  (CLI/Web)  │
       └─────────────┘           └────────────┘
       一級クライアント            一級クライアント
```

UI ではなく API（ビジネスロジック）が主役。LLM も UI も、API 経由で同じ
ビジネスロジックを呼ぶ。

## エージェント連携

```
Phase 0 (準備・並列実行)
├─ GuidelineFetcher       補助金名 → 公募要領PDF・様式docxを自動取得（任意）
└─ AdoptionResearcher     採択事例をWeb調査し skill_store に蓄積（任意）

       ↓

Phase 1 (並列実行)
├─ #5 GuidelineParser     公募要領PDF → 不変条件JSON
├─ #6 FinancialReader     決算書 → PLSummary / BSSummary
└─ #7 ExpenseCalc         見積書 → 経費明細

       ↓ (全部完了で起動)

Phase 2
└─ #8 StoryBuilder        上記を統合 → ApplicationStory

       ↓

Phase 3 (並列実行)
├─ #12 FactChecker        統計データの裏付け検証（Perplexity）
└─ #14 QualityChecker     採択パターン照合・自己採点

       ↓

Phase 4
└─ #13 DocumentBuilder    全部を統合 → 公式テンプレートに流し込み → .docx 出力
```

オーケストレーター（`agents/orchestrator.py`）が依存関係を管理し、リトライ
とエラーハンドリングを担当する。

## Pydantic 駆動の構造化出力

LLM 呼び出しは `tools.claude_client.call_claude_json` 経由で、Claude の
tool_use 機能により**必ずスキーマ準拠の JSON が返る**。

```python
result = await call_claude_json(
    system_prompt=...,
    user_message=...,
    json_schema=GuidelineParseOutput.model_json_schema(),
)
output = GuidelineParseOutput.model_validate(result)
```

- LLM 出力は自然言語の段落ではなく型付きデータ
- ビジネスロジック側で不変条件を機械的にチェック可能
- UI と LLM が同じ型を共有

## 不変条件をどう守るか

申請書の質を担保するのは、各エージェントの賢さよりも**ビジネスロジック側
の不変条件**である。

- 公募要領の経費区分 → 経費明細は必ずいずれかに分類されている
- 文字数上限 → セクションごとに `schemas/section_limits.py` で型化
- 加点要件 → `tools/bonus_points.py` で機械判定（赤字／黒字／賃上げ／環境変化）
- 採択パターン → `agents/quality_check.py` の業界別キーワードと自己採点ルーブリック

LLM がどんな出力をしても、これらの不変条件を破った時点でビジネスロジック層
が拒絶する。

## テンプレート保持

公式の Word 様式（罫線・スタイル・余白）を壊さないために、Document Builder
は**スクラッチビルドではなくテンプレート流し込み式**を採る。

- `templates/<subsidy>/様式X.docx` を読み込み
- `{{section_1_1}}` のような placeholder を内容に置換
- 表の罫線・Heading の連番・既定フォントはテンプレ側が保持

## 自己改善層

`tools/skill_store.py` がローカルファイルシステムに JSONL で蓄積：

- `ExecutionLog` — 全エージェント実行ログ
- `SkillEntry` — 抽出された know-how、スコア付き
- `FeedbackInput` — 採択／不採択結果
- `knowledge/*.json` — 業種別・補助金別の自由形式ナレッジ

採択フィードバックが入るたび、該当 skill のスコアが ±0.10 で更新され、版
管理される。次回の検索でスコアが高い skill が優先的に注入される。
