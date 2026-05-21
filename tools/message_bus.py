"""Inter-agent message bus stub (public version).

The full LINE WORKS / persona-based organization layer lives in the private
build. This stub satisfies imports and acts as a no-op so the core
application pipeline runs without messaging side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    HUMAN_INPUT_REQUIRED = "human_input_required"


@dataclass
class AgentMessage:
    sender: str
    recipient: str
    message_type: MessageType
    content: str
    metadata: dict[str, Any] | None = None


class _StubMessageBus:
    def send(self, _msg: AgentMessage) -> None:
        return None


message_bus = _StubMessageBus()
