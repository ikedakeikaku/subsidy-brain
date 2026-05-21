"""#4 オーケストレーター

全エージェント間のデータ受け渡し、Step間の依存関係管理、進捗管理、エラーハンドリング。
各Stepが完了し次にどのエージェントを起動すべきかを判断する司令塔。

Step依存関係:
  Step 0 (#5, #6, #7) → Step 1 (#8) → Step 2 (#9) + Step 3 (#10, #11) →
  → #12 (after #10) → Step 4 (#13 → #14 → #15)
"""

import logging

from agents.base import BaseAgent
from schemas.orchestrator import (
    NextAction,
    OrchestratorInput,
    OrchestratorOutput,
    ProjectStatus,
    StepCompletion,
)

logger = logging.getLogger(__name__)

# エージェントごとのStep割り当て
_STEP_AGENTS = {
    "step0": {"#5", "#6", "#7"},
    "step1": {"#8"},
    "step2": {"#9"},
    "step3": {"#10", "#11", "#12"},
    "step4": {"#13", "#14", "#15"},
}

# 最大リトライ回数
_MAX_RETRIES = 3


class Orchestrator(BaseAgent):
    """#4 オーケストレーター"""

    agent_id = "#4"
    agent_name = "オーケストレーター"

    def __init__(self):
        super().__init__()
        # 案件ごとの完了エージェントを追跡（実運用ではDB/Sheetsに永続化）
        self._completed: dict[str, set[str]] = {}
        # 案件ごとの過去申請コンテキスト（再申請時に後続エージェントへ渡す）
        self._past_application_context: dict[str, dict] = {}

    def set_past_application(self, applicant_id: str, past_app: dict) -> None:
        """案件に前回申請の情報を設定する（start_projectから呼ばれる）。"""
        self._past_application_context[applicant_id] = past_app

    async def _execute_impl(self, input_data: OrchestratorInput) -> OrchestratorOutput:
        self.logger.info(
            "オーケストレーター: event=%s, source=%s, applicant=%s",
            input_data.event_type,
            input_data.source_agent,
            input_data.applicant_id,
        )

        try:
            if input_data.event_type == "agent_completed":
                return self._handle_agent_completed(input_data)
            elif input_data.event_type == "agent_error":
                return self._handle_agent_error(input_data)
            elif input_data.event_type == "human_input":
                return self._handle_human_input(input_data)
            elif input_data.event_type == "schedule":
                return self._handle_schedule(input_data)
            else:
                raise ValueError(f"不明なイベントタイプ: {input_data.event_type}")

        except Exception as e:
            await self.on_error(e)
            raise

    def _handle_agent_completed(
        self, input_data: OrchestratorInput
    ) -> OrchestratorOutput:
        """エージェント完了イベント。次に起動すべきエージェントを判断。"""
        aid = input_data.applicant_id
        source = input_data.source_agent

        # 完了を記録
        if aid not in self._completed:
            self._completed[aid] = set()
        self._completed[aid].add(source)

        completed = self._completed[aid]
        next_actions = []

        # Step 0 → Step 1
        step0_agents = _STEP_AGENTS["step0"]
        if step0_agents.issubset(completed) and "#8" not in completed:
            story_payload: dict = {"action": "init"}
            # 再申請の場合、過去申請情報を渡す
            past_ctx = self._past_application_context.get(aid)
            if past_ctx:
                story_payload["past_application"] = past_ctx
            next_actions.append(
                NextAction(
                    target_agent="#8",
                    action="start",
                    input_payload=story_payload,
                    priority="high",
                )
            )

        # Step 1 → Step 2 + Step 3
        if "#8" in completed:
            if "#9" not in completed:
                next_actions.append(
                    NextAction(
                        target_agent="#9",
                        action="start",
                        priority="normal",
                    )
                )
            if "#10" not in completed:
                next_actions.append(
                    NextAction(
                        target_agent="#10",
                        action="start",
                        priority="normal",
                    )
                )
            if "#11" not in completed:
                next_actions.append(
                    NextAction(
                        target_agent="#11",
                        action="start",
                        priority="normal",
                    )
                )

        # #10 完了 → #12
        if "#10" in completed and "#12" not in completed:
            next_actions.append(
                NextAction(
                    target_agent="#12",
                    action="start",
                    priority="normal",
                )
            )

        # Step 2 + Step 3 → Step 4
        step23_agents = _STEP_AGENTS["step2"] | _STEP_AGENTS["step3"]
        if step23_agents.issubset(completed) and "#13" not in completed:
            next_actions.append(
                NextAction(
                    target_agent="#13",
                    action="start",
                    priority="high",
                )
            )

        # #13 → #14
        if "#13" in completed and "#14" not in completed:
            quality_payload: dict = {}
            # 再申請の場合、前回不採択情報を品質チェックに渡す
            past_ctx = self._past_application_context.get(aid)
            if past_ctx:
                quality_payload["past_rejection"] = {
                    "rejection_reasons": past_ctx.get("rejection_reasons", []),
                    "past_story": past_ctx.get("past_story", {}),
                    "consultant_notes": past_ctx.get("consultant_notes", ""),
                }
            next_actions.append(
                NextAction(
                    target_agent="#14",
                    action="start",
                    input_payload=quality_payload,
                    priority="high",
                )
            )

        # #14 → #15
        if "#14" in completed and "#15" not in completed:
            next_actions.append(
                NextAction(
                    target_agent="#15",
                    action="start",
                    priority="high",
                )
            )

        status = self._build_project_status(aid)

        self.logger.info(
            "次アクション: %d件, ステップ: %s",
            len(next_actions),
            status.current_step,
        )
        return OrchestratorOutput(
            next_actions=next_actions,
            project_status=status,
        )

    def _handle_agent_error(
        self, input_data: OrchestratorInput
    ) -> OrchestratorOutput:
        """エージェントエラー。リトライ判断。"""
        error = input_data.error
        retry_count = error.retry_count if error else 0

        if retry_count < _MAX_RETRIES:
            return OrchestratorOutput(
                next_actions=[
                    NextAction(
                        target_agent=input_data.source_agent,
                        action="retry",
                        input_payload=input_data.payload,
                        priority="high",
                    )
                ],
                project_status=self._build_project_status(input_data.applicant_id),
            )
        else:
            # 3回失敗 → コンサル通知
            self.logger.warning(
                "%s が%d回失敗。コンサルにアラート。",
                input_data.source_agent,
                _MAX_RETRIES,
            )
            status = self._build_project_status(input_data.applicant_id)
            status.blockers.append(
                f"{input_data.source_agent} が{_MAX_RETRIES}回失敗: "
                f"{error.message if error else '不明'}"
            )
            return OrchestratorOutput(
                next_actions=[],
                project_status=status,
            )

    def _handle_human_input(
        self, input_data: OrchestratorInput
    ) -> OrchestratorOutput:
        """人間の入力。指定されたエージェントを起動。"""
        target = input_data.payload.get("target_agent", "")
        if target:
            return OrchestratorOutput(
                next_actions=[
                    NextAction(
                        target_agent=target,
                        action="start",
                        input_payload=input_data.payload,
                        priority="high",
                    )
                ],
                project_status=self._build_project_status(input_data.applicant_id),
            )
        return OrchestratorOutput(
            project_status=self._build_project_status(input_data.applicant_id)
        )

    def _handle_schedule(
        self, input_data: OrchestratorInput
    ) -> OrchestratorOutput:
        """スケジュールイベント。ステータスチェック。"""
        return OrchestratorOutput(
            project_status=self._build_project_status(input_data.applicant_id)
        )

    def _build_project_status(self, applicant_id: str) -> ProjectStatus:
        """プロジェクトステータスを構築する。"""
        completed = self._completed.get(applicant_id, set())

        def _step_completion(step_name: str) -> float:
            agents = _STEP_AGENTS.get(step_name, set())
            if not agents:
                return 0.0
            return len(completed & agents) / len(agents)

        step_comp = StepCompletion(
            step0=_step_completion("step0"),
            step1=_step_completion("step1"),
            step2=_step_completion("step2"),
            step3=_step_completion("step3"),
            step4=_step_completion("step4"),
        )

        # 現在のステップを判定
        if step_comp.step4 > 0:
            current = "step4"
        elif step_comp.step3 > 0 or step_comp.step2 > 0:
            current = "step3"
        elif step_comp.step1 > 0:
            current = "step1"
        elif step_comp.step0 > 0:
            current = "step0"
        else:
            current = "step0"

        return ProjectStatus(
            current_step=current,
            completion=step_comp,
        )
