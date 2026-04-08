from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol


@dataclass
class ToolContext:
    user_id: str
    emotion_tag: str
    now_iso: str
    conversation_window: list[dict[str, Any]]
    recent_memory: list[dict[str, Any]]
    confidence_budget: float = 1.0


@dataclass
class ToolSpec:
    name: str
    version: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    safety: Dict[str, Any]


class ChatTool(Protocol):
    spec: ToolSpec

    def run(self, context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        ...

