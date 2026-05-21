# Architecture

## AI-Nativeなビジネスロジック中心設計

中島聡氏が2026-05-19号で書いた構造をベースにしています。

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
       同じビジネスロジックを通る
```

**UIではなくAPI（=ビジネスロジック）が主役**。LLMもUIも、API経由で同じビジネスロジックを呼ぶ対等なクライアント。これによりLLMで生成した申請書とUIで編集した申請書のあいだに、構造的なズレが発生しません。

## エージェント連携

```
Step 0 (並列実行)
├─ #a05 GuidelineParser   公募要領PDF → 不変条件JSON
├─ #a06 FinancialReader   決算書 → PLSummary / BSSummary
└─ #a07 ExpenseCalc       見積書 → 経費明細

       ↓ (全部完了で起動)

Step 1
└─ #a08 StoryBuilder      上記3つを統合 → ApplicationStory（4-2やKPI含む論理構造）

       ↓

Step 2 (並列実行)
├─ #a12 FactChecker       統計データの裏付け検証（Perplexity）
└─ #a14 QualityChecker    採択パターン照合・自己採点

       ↓

Step 3
└─ #a13 DocumentBuilder   全部を統合 → Word出力
```

オーケストレーター（`agents/orchestrator.py`）が Step依存関係を管理し、リトライ・エラーハンドリングを担当します。これが中島氏のいう「AIスーパー番頭」（2024-12-24号）に相当します。

## Pydantic駆動の構造化出力

LLM呼び出しはすべて `tools.claude_client.call_claude_json` 経由で、Claude の tool_use 機能を使って**必ずスキーマ準拠のJSON**を返します。

```python
# tools/claude_client.py のパターン
result = await call_claude_json(
    system_prompt=...,
    user_message=...,
    json_schema=GuidelineParseOutput.model_json_schema(),
)
output = GuidelineParseOutput.model_validate(result)
```

これにより：

- LLM出力が「自然言語の段落」ではなく**型付きデータ**になる
- ビジネスロジック側で不変条件をチェックできる
- UIとLLMが同じ型を共有できる（クライアント対等の核心）

中島氏が2026-05-19号で「型付きのスキーマを読んで確実にAPIを呼び出せるLLMが普通に手に入るようになった」と書いた条件の上に成り立っています。

## 不変条件をどう守るか

申請書の質を担保しているのは、各エージェントの賢さよりも**ビジネスロジック側の不変条件**です。

- 公募要領の経費区分 → 経費明細は必ずいずれかに分類されている
- 文字数上限 → セクションごとに `schemas/section_limits.py` で型化
- 加点要件 → `tools/bonus_points.py` で機械判定（赤字／黒字／賃上げ／環境変化）
- 採択パターン → `agents/quality_check.py` の業界別キーワードと自己採点ルーブリック

LLMがどんな出力をしても、これらの不変条件を破った時点でビジネスロジック層が拒絶する設計です。
