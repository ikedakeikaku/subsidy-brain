"""ペルソナスキーマ定義

各エージェントに名前・性格・専門性を持たせるペルソナシステム。
"""

from pydantic import BaseModel, Field


class Persona(BaseModel):
    """エージェントのペルソナ定義"""

    name: str = Field(..., description="ペルソナ名（例: サトシ）")
    role: str = Field(..., description="役割（例: 審査員目線レビュワー）")
    personality: str = Field(..., description="性格特性")
    expertise: list[str] = Field(default_factory=list, description="専門領域")
    system_prompt: str = Field(default="", description="Claude APIに渡すシステムプロンプト")
    avatar_emoji: str = Field(default="🤖", description="アバター絵文字")
    response_style: str = Field(default="", description="応答スタイルの説明")


class PersonaUpdate(BaseModel):
    """ペルソナ更新用"""

    name: str | None = None
    role: str | None = None
    personality: str | None = None
    expertise: list[str] | None = None
    system_prompt: str | None = None
    avatar_emoji: str | None = None
    response_style: str | None = None


class PersonaRegistry:
    """agent_id → Persona のマッピングを管理"""

    def __init__(self) -> None:
        self._personas: dict[str, Persona] = {}

    def register(self, agent_id: str, persona: Persona) -> None:
        self._personas[agent_id] = persona

    def get(self, agent_id: str) -> Persona | None:
        return self._personas.get(agent_id)

    def get_all(self) -> dict[str, Persona]:
        return dict(self._personas)

    def update(self, agent_id: str, update: PersonaUpdate) -> Persona | None:
        persona = self._personas.get(agent_id)
        if persona is None:
            return None
        data = persona.model_dump()
        update_data = update.model_dump(exclude_none=True)
        data.update(update_data)
        updated = Persona(**data)
        self._personas[agent_id] = updated
        return updated

    def resolve_name(self, name: str) -> str | None:
        """ペルソナ名からagent_idを解決する。"""
        for agent_id, persona in self._personas.items():
            if persona.name == name:
                return agent_id
        return None

    def get_all_names(self) -> dict[str, str]:
        """ペルソナ名 → agent_id のマッピングを返す。"""
        return {p.name: aid for aid, p in self._personas.items()}
