"""全エージェントの基底クラス"""

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod

import anthropic
import httpx
from pydantic import BaseModel

from config.settings import settings
from schemas.persona import Persona


class BaseAgent(ABC):
    """全エージェントが継承する基底クラス。"""

    agent_id: str  # "#5" など
    agent_name: str  # "要領パーサー" など

    # サブクラスでTrueにすると、スキル注入対象となる
    skill_injection_target: bool = False

    def __init__(self):
        self.logger = logging.getLogger(f"agent.{self.agent_id}")
        self._injected_skill_ids: list[str] = []
        self.persona: Persona | None = None

    def set_persona(self, persona: Persona) -> None:
        """ペルソナを設定する。"""
        self.persona = persona

    def get_persona_system_prompt(self) -> str:
        """ペルソナのシステムプロンプトを返す。未設定時は空文字。"""
        if self.persona:
            return self.persona.system_prompt
        return ""

    def format_response(self, text: str) -> str:
        """ペルソナ名を冒頭に付与した応答を返す。"""
        if self.persona:
            return f"{self.persona.name}: {text}"
        return text

    # ============================================================
    # エージェント間会話
    # ============================================================

    def send_message(
        self,
        case_id: str,
        content: str,
        to_agent: str | None = None,
        message_type: str = "opinion",
        context: dict | None = None,
        reply_to: str | None = None,
    ) -> str:
        """メッセージバスにメッセージを送信する。"""
        from tools.message_bus import AgentMessage, MessageType, message_bus

        msg = AgentMessage(
            case_id=case_id,
            from_agent=self.agent_id,
            from_name=self.persona.name if self.persona else self.agent_name,
            to_agent=to_agent,
            message_type=MessageType(message_type),
            content=self.format_response(content),
            context=context or {},
            reply_to=reply_to,
        )
        return message_bus.send(msg)

    def broadcast_opinion(self, case_id: str, opinion: str, context: dict | None = None) -> str:
        """全エージェントに意見を共有する。"""
        return self.send_message(case_id, opinion, to_agent=None, message_type="opinion", context=context)

    def ask_agent(self, case_id: str, target_agent_id: str, question: str, context: dict | None = None) -> str:
        """別のエージェントに質問する。"""
        return self.send_message(case_id, question, to_agent=target_agent_id, message_type="question", context=context)

    def respond_to(self, case_id: str, message_id: str, response: str) -> str:
        """メッセージへの返答。"""
        return self.send_message(case_id, response, message_type="response", reply_to=message_id)

    @abstractmethod
    async def _execute_impl(self, input_data: BaseModel) -> BaseModel:
        """サブクラスが実装するメイン処理。"""
        pass

    async def execute(self, input_data: BaseModel) -> BaseModel:
        """メイン処理。スキル注入 + 実行ログを自動記録する。"""
        from schemas.skill import ExecutionLog
        from tools.skill_store import skill_store

        start_ms = time.monotonic_ns() // 1_000_000
        self._injected_skill_ids = []

        # スキル注入
        skill_injected = False
        if self.skill_injection_target and settings.skill_injection_enabled:
            skill_injected = await self._inject_skills(input_data)

        # 入力ハッシュ
        input_dict = input_data.model_dump(mode="json") if hasattr(input_data, "model_dump") else {}
        input_json = json.dumps(input_dict, sort_keys=True, default=str)
        input_hash = hashlib.sha256(input_json.encode()).hexdigest()[:16]
        input_summary = input_json[:200]

        applicant_id = getattr(input_data, "applicant_id", "") or ""

        try:
            result = await self._execute_impl(input_data)
        except anthropic.RateLimitError as e:
            self.logger.warning("[%s] Claude API レート制限: %s（tenacityリトライ後も失敗）", self.agent_id, e)
            await self.on_error(e)
            raise
        except anthropic.AuthenticationError as e:
            self.logger.error("[%s] Claude API 認証エラー（APIキーを確認）: %s", self.agent_id, e)
            await self.on_error(e)
            raise
        except (anthropic.APIConnectionError, httpx.ConnectError) as e:
            self.logger.warning("[%s] 接続エラー（ネットワークを確認）: %s", self.agent_id, e)
            await self.on_error(e)
            raise
        except (ValueError, KeyError) as e:
            self.logger.error("[%s] データ処理エラー: %s", self.agent_id, e, exc_info=True)
            await self.on_error(e)
            raise
        except Exception as e:
            self.logger.error("[%s] 予期しないエラー: %s", self.agent_id, e, exc_info=True)
            await self.on_error(e)
            raise

        # 出力ハッシュ・サマリー
        output_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
        output_json = json.dumps(output_dict, sort_keys=True, default=str)
        output_hash = hashlib.sha256(output_json.encode()).hexdigest()[:16]
        output_summary = output_json[:200]

        duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

        log = ExecutionLog(
            applicant_id=applicant_id,
            agent_id=self.agent_id,
            input_hash=input_hash,
            output_hash=output_hash,
            input_summary=input_summary,
            output_summary=output_summary,
            duration_ms=duration_ms,
            skill_injected=skill_injected,
            used_skill_ids=list(self._injected_skill_ids),
        )
        skill_store.save_execution_log(log)
        self.logger.debug("実行ログ記録: %s (%dms, skill_injected=%s)", log.id, duration_ms, skill_injected)

        return result

    async def _inject_skills(self, input_data: BaseModel) -> bool:
        """スキルストアから関連スキルを検索し、注入準備をする。"""
        from tools.skill_formatter import format_as_few_shot
        from tools.skill_store import skill_store

        inject_start = time.monotonic_ns() // 1_000_000

        input_dict = input_data.model_dump(mode="json") if hasattr(input_data, "model_dump") else {}
        skills = skill_store.search_similar_skills(self.agent_id, input_dict)

        if not skills:
            return False

        # 使用回数をインクリメント
        for skill in skills:
            skill_store.increment_usage(skill.id)
            self._injected_skill_ids.append(skill.id)

        # few-shot形式に整形して _skill_context に保存
        self._skill_context = format_as_few_shot(skills)

        inject_ms = (time.monotonic_ns() // 1_000_000) - inject_start
        self.logger.info(
            "スキル注入: %d件 (%dms)", len(skills), inject_ms
        )
        return True

    def get_skill_context(self) -> str:
        """注入されたスキルコンテキストを返す。サブクラスがプロンプト構築時に使用。"""
        return getattr(self, "_skill_context", "")

    async def validate_input(self, input_data: BaseModel) -> bool:
        """入力バリデーション。Pydanticが自動で行うが追加チェック用。"""
        return True

    async def on_error(self, error: Exception) -> dict:
        """エラー時のハンドリング。#4オーケストレーターに通知。"""
        self.logger.error(f"[{self.agent_id} {self.agent_name}] {error}", exc_info=True)
        return {
            "agent_id": self.agent_id,
            "error_code": type(error).__name__,
            "message": str(error),
            "retry_count": 0,
        }
