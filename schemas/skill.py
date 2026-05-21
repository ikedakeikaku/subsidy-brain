"""スキルストア・実行ログスキーマ"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SkillType(str, Enum):
    """スキル種別"""
    PROMPT_SKILL = "prompt_skill"
    KNOWLEDGE = "knowledge"
    QUALITY_RUBRIC = "quality_rubric"
    EXPENSE_PATTERN = "expense_pattern"
    REVISION_PATTERN = "revision_pattern"


class SkillEntry(BaseModel):
    """スキルストアの1エントリ"""
    id: str = Field(default="", description="スキルID")
    skill_type: SkillType
    agent_id: str = Field(..., description="対象エージェントID")
    industry: str | None = Field(default=None, description="業種")
    subsidy_type: str | None = Field(default=None, description="補助金種別")
    content: dict = Field(default_factory=dict, description="スキル内容")
    score: float = Field(default=0.5, ge=0.0, le=1.0, description="スキルスコア")
    version: int = Field(default=1, description="バージョン")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    usage_count: int = Field(default=0, description="使用回数")


class ExecutionLog(BaseModel):
    """エージェント実行ログ"""
    id: str = Field(default="", description="ログID")
    applicant_id: str = Field(..., description="申請者ID")
    agent_id: str = Field(..., description="エージェントID")
    input_hash: str = Field(default="", description="入力データハッシュ")
    output_hash: str = Field(default="", description="出力データハッシュ")
    input_summary: str = Field(default="", description="入力サマリー")
    output_summary: str = Field(default="", description="出力サマリー")
    quality_score: float | None = Field(default=None, description="品質スコア")
    duration_ms: int = Field(default=0, description="実行時間(ms)")
    token_usage: dict = Field(default_factory=dict, description="トークン使用量")
    created_at: datetime = Field(default_factory=datetime.now)
    skill_injected: bool = Field(default=False, description="スキル注入されたか")
    used_skill_ids: list[str] = Field(default_factory=list, description="使用スキルID")


class SkillSearchQuery(BaseModel):
    """スキル検索クエリ"""
    agent_id: str
    industry: str | None = None
    subsidy_type: str | None = None
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)
    limit: int = Field(default=5, ge=1, le=50)


class FeedbackInput(BaseModel):
    """人間からのフィードバック"""
    applicant_id: str = Field(..., description="申請者ID")
    adopted: bool = Field(..., description="採択されたか")
    reviewer_comments: str = Field(default="", description="審査員コメント")
