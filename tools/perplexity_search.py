"""Perplexity API を利用した市場調査ツール

マクロ（業界全体）とミクロ（商圏）の2方面から市場データを取得し、
公的統計と照合して正確性を検証する。
"""

import logging
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings

logger = logging.getLogger(__name__)

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"


@dataclass
class SearchResult:
    """Perplexity検索結果"""

    content: str
    citations: list[str]


class PerplexitySearchError(Exception):
    """Perplexity API呼び出しエラー"""


_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
)


@_RETRY_DECORATOR
async def _call_perplexity(
    system_prompt: str,
    user_message: str,
    model: str = "sonar",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> SearchResult:
    """Perplexity APIを呼び出す。

    Args:
        system_prompt: システムプロンプト。
        user_message: ユーザーメッセージ（検索クエリ）。
        model: 使用モデル（sonar / sonar-pro）。
        temperature: 温度。事実検索は低温推奨。
        max_tokens: 最大トークン数。

    Returns:
        SearchResult（応答テキスト + 引用URL一覧）。
    """
    api_key = settings.perplexity_api_key
    if not api_key:
        raise PerplexitySearchError("PERPLEXITY_API_KEY が設定されていません")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(PERPLEXITY_API_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise PerplexitySearchError(
                f"Perplexity API error {resp.status_code}: {resp.text}"
            )
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])
    return SearchResult(content=content, citations=citations)


# ============================================================
# 市場調査用の高レベル関数
# ============================================================

_MACRO_SYSTEM = """\
あなたは日本の産業・市場統計に精通した調査アナリストです。
以下のルールを厳守してください:
- 経産省・中小企業白書・業界団体の公的統計を優先して引用する
- 全ての数値に出典（統計名・発行年・URL）を付与する
- 市場規模は「約XX億円」形式で記載する
- 成長率・CAGR等の推移データがあれば含める
- 出典が見つからない数値は「推定値」と明記する

回答はJSON形式で出力してください:
```json
{
  "market_size": {"value": "約X,XXX億円", "year": 2024, "source": "出典名", "url": "..."},
  "growth_rate": {"value": "年率X.X%", "period": "20XX-20XX", "source": "...", "url": "..."},
  "industry_trends": [
    {"trend": "トレンド内容", "source": "出典名", "url": "..."}
  ],
  "key_statistics": [
    {"stat": "統計内容", "value": "数値", "source": "出典名", "url": "..."}
  ]
}
```"""

_MICRO_SYSTEM = """\
あなたは日本の地域経済・商圏分析に精通した調査アナリストです。
以下のルールを厳守してください:
- RESAS人口データ・総務省家計調査・地域事業所数等の公的統計を使用する
- フェルミ推定の計算式を明示する（商圏人口×世帯数×品目支出額×シェア等）
- 全ての数値に出典を付与する
- 地域特有の経済動向・人口動態を含める

回答はJSON形式で出力してください:
```json
{
  "trade_area": {
    "population": {"value": "X万人", "source": "RESAS / 国勢調査", "url": "..."},
    "households": {"value": "X万世帯", "source": "...", "url": "..."},
    "establishments": {"value": "X件", "source": "経済センサス", "url": "..."}
  },
  "fermi_estimation": {
    "formula": "商圏人口 × 世帯数 × 品目支出額 × 想定シェア",
    "steps": [
      {"step": "ステップ説明", "value": "数値", "source": "..."}
    ],
    "result": "推定市場規模: 約X億円"
  },
  "regional_trends": [
    {"trend": "地域動向", "source": "出典名", "url": "..."}
  ]
}
```"""

_CROSS_CHECK_SYSTEM = """\
あなたはファクトチェック専門のアナリストです。
2つの情報源（マクロ分析・ミクロ分析）の結果を照合し、矛盾点や整合性を検証してください。
不一致がある場合は公的統計を優先し、乖離の理由を推察してください。

回答はJSON形式で出力してください:
```json
{
  "consistency_score": 0.85,
  "verified_facts": [
    {"fact": "検証済み事実", "macro_source": "...", "micro_source": "...", "status": "consistent"}
  ],
  "discrepancies": [
    {"item": "不一致項目", "macro_value": "...", "micro_value": "...", "resolution": "解決方法"}
  ],
  "recommendations": ["補足調査の推奨事項"]
}
```"""


async def search_macro(business_type: str, keywords: list[str]) -> SearchResult:
    """マクロ分析: 業界全体の市場規模・動向を検索する。

    Args:
        business_type: 業種（例: "美容室", "飲食店"）。
        keywords: 追加の検索キーワード。

    Returns:
        SearchResult。
    """
    query = (
        f"日本の{business_type}業界について以下を調査してください:\n"
        f"1. 市場規模（直近の公的統計）\n"
        f"2. 成長率・市場推移\n"
        f"3. 業界トレンド・課題\n"
        f"4. 関連する公的統計データ\n"
    )
    if keywords:
        query += f"\n関連キーワード: {', '.join(keywords)}"

    logger.info("マクロ分析検索: business_type=%s", business_type)
    return await _call_perplexity(
        system_prompt=_MACRO_SYSTEM,
        user_message=query,
        model="sonar-pro",
    )


async def search_micro(
    business_type: str,
    location: str,
    trade_area_km: float = 5.0,
    target_segment: str = "",
) -> SearchResult:
    """ミクロ分析: 商圏の人口・世帯・支出データからフェルミ推定を行う。

    Args:
        business_type: 業種。
        location: 所在地（例: "東京都世田谷区"）。
        trade_area_km: 商圏半径（km）。
        target_segment: ターゲット顧客層。

    Returns:
        SearchResult。
    """
    query = (
        f"{location}周辺（半径{trade_area_km}km商圏）の{business_type}について:\n"
        f"1. 商圏人口・世帯数（RESAS / 国勢調査）\n"
        f"2. {business_type}関連の家計支出額（総務省家計調査）\n"
        f"3. 同業の事業所数（経済センサス）\n"
        f"4. フェルミ推定で商圏市場規模を算出してください\n"
    )
    if target_segment:
        query += f"\nターゲット顧客層: {target_segment}"

    logger.info("ミクロ分析検索: location=%s, business_type=%s", location, business_type)
    return await _call_perplexity(
        system_prompt=_MICRO_SYSTEM,
        user_message=query,
        model="sonar-pro",
    )


async def cross_check(macro_result: str, micro_result: str) -> SearchResult:
    """マクロ・ミクロ分析結果のクロスチェックを行う。

    Args:
        macro_result: マクロ分析の結果テキスト。
        micro_result: ミクロ分析の結果テキスト。

    Returns:
        SearchResult（整合性検証結果）。
    """
    query = (
        f"以下の2つの分析結果を照合し、整合性を検証してください。\n\n"
        f"【マクロ分析（業界全体）】\n{macro_result}\n\n"
        f"【ミクロ分析（商圏）】\n{micro_result}"
    )

    logger.info("クロスチェック実行")
    return await _call_perplexity(
        system_prompt=_CROSS_CHECK_SYSTEM,
        user_message=query,
    )


async def search_market_full(
    business_type: str,
    location: str,
    keywords: list[str] | None = None,
    trade_area_km: float = 5.0,
    target_segment: str = "",
) -> dict:
    """マクロ+ミクロ+クロスチェックの一括市場調査を実行する。

    Args:
        business_type: 業種。
        location: 所在地。
        keywords: 追加キーワード。
        trade_area_km: 商圏半径（km）。
        target_segment: ターゲット顧客層。

    Returns:
        {"macro": SearchResult, "micro": SearchResult, "cross_check": SearchResult,
         "all_citations": list[str]}
    """
    keywords = keywords or []

    # マクロ・ミクロを並行実行
    import asyncio

    macro_result, micro_result = await asyncio.gather(
        search_macro(business_type, keywords),
        search_micro(business_type, location, trade_area_km, target_segment),
    )

    # クロスチェック
    check_result = await cross_check(macro_result.content, micro_result.content)

    # 全引用URLを集約・重複排除
    all_citations = list(dict.fromkeys(
        macro_result.citations + micro_result.citations + check_result.citations
    ))

    logger.info(
        "市場調査完了: 引用URL %d件",
        len(all_citations),
    )

    return {
        "macro": macro_result,
        "micro": micro_result,
        "cross_check": check_result,
        "all_citations": all_citations,
    }
