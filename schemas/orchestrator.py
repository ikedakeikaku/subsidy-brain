"""#4 オーケストレーター 入出力スキーマ

全エージェント間のデータ受け渡し、Step間の依存関係管理、進捗管理。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Input ---


class OrchestratorError(BaseModel):
    """エラー情報"""

    code: str = Field(default="", description="エラーコード")
    message: str = Field(default="", description="エラーメッセージ")
    retry_count: int = Field(default=0, description="リトライ回数")


class OrchestratorInput(BaseModel):
    """#4 オーケストレーター入力"""

    event_type: str = Field(
        ...,
        description="agent_completed | agent_error | human_input | schedule",
    )
    source_agent: str = Field(default="", description="イベント元エージェントID")
    applicant_id: str = Field(..., description="申請者ID")
    payload: dict = Field(default_factory=dict, description="イベントペイロード")
    error: OrchestratorError | None = Field(default=None, description="エラー情報")


# --- Output ---


class NextAction(BaseModel):
    """次に実行するアクション"""

    target_agent: str = Field(..., description="対象エージェントID")
    action: str = Field(default="start", description="start | retry | skip")
    input_payload: dict = Field(default_factory=dict, description="入力データ")
    priority: str = Field(default="normal", description="high | normal | low")


class StepCompletion(BaseModel):
    """Step完了状況"""

    step0: float = Field(default=0.0, description="Step 0 完了率")
    step1: float = Field(default=0.0, description="Step 1 完了率")
    step2: float = Field(default=0.0, description="Step 2 完了率")
    step3: float = Field(default=0.0, description="Step 3 完了率")
    step4: float = Field(default=0.0, description="Step 4 完了率")


class ProjectStatus(BaseModel):
    """プロジェクトステータス"""

    current_step: str = Field(default="step0", description="現在のステップ")
    completion: StepCompletion = Field(
        default_factory=StepCompletion, description="Step別完了率"
    )
    blockers: list[str] = Field(default_factory=list, description="ブロッカー一覧")
    estimated_completion: str = Field(default="", description="完了見込み日時")


class OrchestratorOutput(BaseModel):
    """#4 オーケストレーター出力"""

    next_actions: list[NextAction] = Field(
        default_factory=list, description="次アクション一覧"
    )
    project_status: ProjectStatus = Field(
        default_factory=ProjectStatus, description="プロジェクトステータス"
    )
