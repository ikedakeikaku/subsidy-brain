"""共通型定義"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """エージェント処理ステータス"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"
    RETRY = "retry"


class ApplicantType(str, Enum):
    """申請者種別"""
    INDIVIDUAL = "個人事業主"
    CORPORATION = "法人"


class SubsidyType(str, Enum):
    """補助金枠種別"""
    REGULAR = "通常枠"
    GROWTH = "成長枠"
    STANDARD = "標準枠"


class AgentError(BaseModel):
    """エージェントエラー通知"""
    agent_id: str
    error_code: str
    message: str
    retry_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)


class AgentResult(BaseModel):
    """エージェント実行結果の共通ラッパー"""
    agent_id: str
    status: AgentStatus
    started_at: datetime
    completed_at: datetime | None = None
    error: AgentError | None = None
    output_ref: str | None = None  # Google Drive等の出力参照先


class ApplicantInfo(BaseModel):
    """申請者基本情報"""
    applicant_id: str
    business_name: str = Field(..., description="事業者名")
    applicant_type: ApplicantType
    representative: str = Field(default="", description="代表者名")
    address: str = Field(default="", description="所在地")
    phone: str = Field(default="", description="電話番号")
    email: str = Field(default="", description="メールアドレス")
    business_description: str = Field(default="", description="事業概要")
    subsidy_round: int = Field(default=0, description="公募回次")
    subsidy_type: SubsidyType = SubsidyType.REGULAR
