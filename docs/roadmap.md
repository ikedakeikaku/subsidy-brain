# Roadmap

## Phase 1 — 補助金申請ドラフトの一気通貫生成（現在）

**ゴール：** 公募要領＋ヒアリング → 申請書Wordドラフト、をエージェント連携で出す。

実装済み（このリポジトリ）：

- 7体のエージェント＋オーケストレーター
  - `agents/guideline_parser.py` — 公募要領を不変条件として構造化
  - `agents/financial_reader.py` — 決算データ取り込み
  - `agents/expense_calc.py` — 経費計算と区分照合
  - `agents/story_builder.py` — 事業ストーリーをLLMで構築
  - `agents/fact_checker.py` — Perplexity連携で統計データ検証
  - `agents/document_builder.py` — python-docxでWord組立
  - `agents/quality_check.py` — 採択パターン照合と自己採点
  - `agents/orchestrator.py` — Step依存関係とリトライ制御
- 加点判定（`tools/bonus_points.py`）
- グラフ生成（`tools/chart_tools.py`）

## Phase 2 — CFOビュー（〜7ヶ月）

**ゴール：** 単発の補助金申請から、継続的な経営判断アシスタントへ拡張。

- 月次P/L監視エージェント（前年同月比・予算実績差異）
- 資金繰り予測エージェント（向こう6ヶ月のキャッシュフロー）
- 補助金マッチングエージェント（自社プロファイル × 募集中補助金）
- 銀行融資シミュレーション
- 税務最適化提案

## Phase 3 — データ統合層（〜12ヶ月）

**ゴール：** 中小企業の散在データを、Claude Codeが読めるローカルファイル正規化レイヤーへ。

- freee MCP連携 → ローカルJSON化
- Google Drive / Gmail / LINE MCP連携
- 紙書類のOCR + 構造化（既存 ocr_tools の拡張）
- ベクトルストアなし、ファイルシステムをDBとして扱う（中島氏 2026-04-07号方式）

## Phase 4 — Company Brain（長期）

**ゴール：** 経営判断の横断エージェント。

- 経営会議エージェント（複数の専門エージェントが対話）
- ベンチマーク比較（業界・規模・地域）
- リスク早期警告（売上減少傾向・取引先与信悪化など）

## 12ヶ月の到達点（BootCamp期間）

| 月 | 中身 |
|---|---|
| 1〜3 | このリポをOSSとして整備、サンプルデモを充実、ドキュメント化 |
| 4〜6 | Phase 2 着手：CFOビューの最小実装 |
| 7〜9 | Phase 3 着手：freee MCP + Drive MCP の統合 |
| 10〜12 | Phase 1〜3 を一体化したカンパニーブレイン プロトタイプ発表 |
