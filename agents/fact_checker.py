"""#12 ファクトチェッカー

市場分析の出力に対し、Perplexity 外部検索で原典照合を行い、
Claude で最終判定する2段階検証を実施。
検証不能データに「要確認」フラグ、古いデータに「outdated」フラグを付与する。
"""

import asyncio
import logging
import re
from datetime import date

from agents.base import BaseAgent
from schemas.fact_check import (
    ClaimToVerify,
    FactCheckInput,
    FactCheckOutput,
    FactCheckSummary,
    VerificationResult,
    VerifiedSource,
)
from tools.claude_client import call_claude, parse_json_response

logger = logging.getLogger(__name__)

# データの鮮度基準: 現在年からこの年数以上古ければ outdated
_MAX_DATA_AGE_YEARS = 2

_JUDGE_SYSTEM_PROMPT = """\
あなたは補助金申請書のファクトチェック専門家です。
各主張について、Perplexity外部検索の結果を踏まえて最終判定してください。

判定ルール:
1. Perplexity検索結果で裏付けが取れた場合は status="verified"
2. 正しい値が判明し主張値と異なる場合は status="corrected" + verified_value を記載
3. 検索結果でも裏付けが取れない場合は status="unverifiable" + 理由を記載
4. データの年度が{current_year}年から2年以上古い場合は status="outdated"
5. 公的統計（経済産業省、中小企業庁、総務省統計局等）を最優先で採用
6. citation_text は「出典：XX省『YY白書』ZZZZ年版」の形式にする

出力形式（JSONのみ）:
```json
{{
  "verification_results": [
    {{
      "claim_id": "claim_01",
      "status": "verified | corrected | unverifiable | outdated",
      "verified_value": null,
      "verified_source": {{
        "name": "出典名",
        "url": "https://...",
        "access_date": "{today}",
        "is_public_stats": true
      }},
      "citation_text": "出典：...",
      "notes": "備考"
    }}
  ]
}}
```
"""

# Perplexity不使用時（フォールバック）のプロンプト
_FALLBACK_SYSTEM_PROMPT = """\
あなたは補助金申請書のファクトチェック専門家です。
与えられた主張（claim）の正確性を、あなたの知識に基づいて検証してください。

検証ルール:
1. 公的統計データ（経済産業省、中小企業庁、総務省統計局等）を最優先で確認
2. 数値は原典（白書・統計レポート等）と照合
3. 検証不能な場合は status="unverifiable" とし、理由を記載
4. 古いデータ（3年以上前）は status="outdated" とする
5. 正しい値が判明した場合は status="corrected" とし verified_value を記載
6. citation_text は「出典：XX省『YY白書』ZZZZ年版」の形式にする

重要: 外部検索なしでの検証のため、確信が持てない場合は必ず status="unverifiable" としてください。

出力形式（JSONのみ）:
```json
{
  "verification_results": [
    {
      "claim_id": "claim_01",
      "status": "verified | corrected | unverifiable | outdated",
      "verified_value": null,
      "verified_source": {
        "name": "出典名",
        "url": "https://...",
        "access_date": "",
        "is_public_stats": true
      },
      "citation_text": "出典：...",
      "notes": "備考"
    }
  ]
}
```
"""


class FactChecker(BaseAgent):
    """#12 ファクトチェッカー（Perplexity外部検証付き）"""

    agent_id = "#12"
    agent_name = "ファクトチェッカー"
    skill_injection_target = True

    async def _execute_impl(self, input_data: FactCheckInput) -> FactCheckOutput:
        self.logger.info(
            "ファクトチェック開始: %d件の主張を検証",
            len(input_data.claims_to_verify),
        )

        try:
            if not input_data.claims_to_verify:
                return FactCheckOutput(
                    summary=FactCheckSummary(
                        total=0,
                        reliability_score=1.0,
                    )
                )

            # Perplexityが利用可能か判定
            perplexity_available = self._is_perplexity_available()

            if perplexity_available:
                results = await self._verify_with_perplexity(
                    input_data.claims_to_verify
                )
            else:
                self.logger.warning(
                    "Perplexity APIキー未設定 — Claude単体でのフォールバック検証"
                )
                results = await self._verify_fallback(input_data.claims_to_verify)

            # データ鮮度の追加チェック
            results = self._check_data_freshness(results)

            # 集計
            total = len(results)
            verified = sum(1 for r in results if r.status == "verified")
            corrected = sum(1 for r in results if r.status == "corrected")
            unverifiable = sum(1 for r in results if r.status == "unverifiable")
            outdated = sum(1 for r in results if r.status == "outdated")
            reliability_score = (verified + corrected) / total if total > 0 else 0.0

            output = FactCheckOutput(
                verification_results=results,
                summary=FactCheckSummary(
                    total=total,
                    verified=verified,
                    corrected=corrected,
                    unverifiable=unverifiable,
                    outdated=outdated,
                    reliability_score=round(reliability_score, 2),
                ),
            )

            self.logger.info(
                "ファクトチェック完了: %d件中 verified=%d, corrected=%d, "
                "unverifiable=%d, outdated=%d, 信頼性=%.0f%%",
                total,
                verified,
                corrected,
                unverifiable,
                outdated,
                reliability_score * 100,
            )
            return output

        except Exception as e:
            await self.on_error(e)
            raise

    # ------------------------------------------------------------------
    # Perplexity 外部検証（メイン経路）
    # ------------------------------------------------------------------

    async def _verify_with_perplexity(
        self, claims: list[ClaimToVerify]
    ) -> list[VerificationResult]:
        """Perplexityで外部検索し、Claudeで最終判定する2段階検証。"""
        from tools.perplexity_search import (
            SearchResult,
        )

        # Step 1: 各claimに対してPerplexity検索を並行実行
        search_tasks = [self._search_claim(claim) for claim in claims]
        search_results: list[SearchResult | None] = await asyncio.gather(
            *search_tasks, return_exceptions=True
        )

        # 例外を None に変換
        resolved: list[SearchResult | None] = []
        for r in search_results:
            if isinstance(r, Exception):
                self.logger.warning("Perplexity検索エラー: %s", r)
                resolved.append(None)
            else:
                resolved.append(r)

        # Step 2: Perplexity結果 + 元の主張 → Claude で最終判定
        user_message = self._build_judge_message(claims, resolved)
        today = date.today()
        system_prompt = _JUDGE_SYSTEM_PROMPT.format(
            current_year=today.year, today=today.isoformat()
        )

        response = await call_claude(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
            max_tokens=8192,
        )
        data = parse_json_response(response)
        return self._parse_results(data)

    async def _search_claim(self, claim: ClaimToVerify):
        """1件のclaimについてPerplexity検索を実行する。"""
        from tools.perplexity_search import _call_perplexity

        query = claim.claim_text
        if claim.claimed_value:
            query += f"（{claim.claimed_value}）"
        # 出典名があればクエリに追加して精度向上
        if claim.source.get("name"):
            query += f" {claim.source['name']}"

        system_prompt = (
            "あなたは日本の公的統計・白書・業界データに精通した調査員です。\n"
            "与えられた主張について、最新の公的統計データを検索し、\n"
            "正確な数値・出典名・出典URLを返してください。\n"
            "公的統計（経済産業省、中小企業庁、総務省統計局等）を最優先で引用すること。"
        )

        return await _call_perplexity(
            system_prompt=system_prompt,
            user_message=f"以下の主張の正確性を検証してください:\n{query}",
            model="sonar",
            temperature=0.1,
            max_tokens=2048,
        )

    def _build_judge_message(
        self,
        claims: list[ClaimToVerify],
        search_results: list,
    ) -> str:
        """Perplexity検索結果を含めたClaude判定用メッセージを構築する。"""
        parts = ["以下の主張をPerplexity検索結果と照合し、最終判定してください。\n"]

        for claim, sr in zip(claims, search_results):
            parts.append(f"### {claim.claim_id}")
            parts.append(f"主張: {claim.claim_text}")
            if claim.claimed_value:
                parts.append(f"主張値: {claim.claimed_value}")
            if claim.source:
                parts.append(
                    f"元の出典: {claim.source.get('name', '不明')} "
                    f"({claim.source.get('url', 'なし')})"
                )

            if sr is not None:
                parts.append("\n**Perplexity検索結果:**")
                parts.append(sr.content)
                if sr.citations:
                    parts.append(f"引用URL: {', '.join(sr.citations)}")
            else:
                parts.append("\n**Perplexity検索結果:** 取得失敗")

            parts.append("")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # フォールバック（Perplexity未設定時）
    # ------------------------------------------------------------------

    async def _verify_fallback(
        self, claims: list[ClaimToVerify]
    ) -> list[VerificationResult]:
        """Perplexity未使用時のClaude単体検証（従来動作）。"""
        user_message = self._build_user_message(claims)
        response = await call_claude(
            system_prompt=_FALLBACK_SYSTEM_PROMPT,
            user_message=user_message,
            temperature=0.1,
            max_tokens=8192,
        )
        data = parse_json_response(response)
        return self._parse_results(data)

    # ------------------------------------------------------------------
    # データ鮮度チェック
    # ------------------------------------------------------------------

    def _check_data_freshness(
        self, results: list[VerificationResult]
    ) -> list[VerificationResult]:
        """検証済み結果のデータ年度を確認し、古いものを outdated に変更する。"""
        current_year = date.today().year
        threshold_year = current_year - _MAX_DATA_AGE_YEARS

        updated: list[VerificationResult] = []
        for r in results:
            if r.status in ("verified", "corrected"):
                data_year = self._extract_year(r.citation_text, r.verified_source.name)
                if data_year and data_year < threshold_year:
                    self.logger.info(
                        "データ鮮度警告: %s のデータ年度 %d は基準年 %d より古い",
                        r.claim_id,
                        data_year,
                        threshold_year,
                    )
                    r = r.model_copy(
                        update={
                            "status": "outdated",
                            "notes": (
                                f"{r.notes}; " if r.notes else ""
                            )
                            + f"データ年度({data_year}年)が基準年({threshold_year}年)より古い",
                        }
                    )
            updated.append(r)
        return updated

    @staticmethod
    def _extract_year(citation_text: str, source_name: str) -> int | None:
        """citation_textやsource_nameから年度を抽出する。"""
        # 「2024年版」「2023年度」「(2024)」等のパターン
        for text in (citation_text, source_name):
            if not text:
                continue
            match = re.search(r"(20[12]\d)\s*年", text)
            if match:
                return int(match.group(1))
        return None

    # ------------------------------------------------------------------
    # 共通ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _is_perplexity_available() -> bool:
        """Perplexity API キーが設定されているか確認する。"""
        from config.settings import settings
        return bool(settings.perplexity_api_key)

    @staticmethod
    def _parse_results(data: dict) -> list[VerificationResult]:
        """Claude応答のJSONを VerificationResult リストに変換する。"""
        results = []
        for r in data.get("verification_results", []):
            vs_data = r.get("verified_source", {})
            results.append(
                VerificationResult(
                    claim_id=r.get("claim_id", ""),
                    status=r.get("status", "unverifiable"),
                    verified_value=r.get("verified_value"),
                    verified_source=VerifiedSource(
                        name=vs_data.get("name", ""),
                        url=vs_data.get("url", ""),
                        access_date=vs_data.get("access_date", ""),
                        is_public_stats=vs_data.get("is_public_stats", False),
                    ),
                    citation_text=r.get("citation_text", ""),
                    notes=r.get("notes", ""),
                )
            )
        return results

    def _build_user_message(self, claims: list[ClaimToVerify]) -> str:
        """検証対象からユーザーメッセージを構築する（フォールバック用）。"""
        parts = ["以下の主張を検証してください。\n"]
        for claim in claims:
            src = claim.source
            parts.append(f"### {claim.claim_id}")
            parts.append(f"主張: {claim.claim_text}")
            if claim.claimed_value:
                parts.append(f"主張値: {claim.claimed_value}")
            if src:
                parts.append(f"出典: {src.get('name', '不明')} ({src.get('url', 'なし')})")
            parts.append("")
        return "\n".join(parts)
