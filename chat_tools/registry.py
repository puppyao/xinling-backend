from __future__ import annotations

from typing import Dict

from chat_tools.base import ChatTool
from chat_tools.mood_update_tool import MoodUpdateTool
from chat_tools.prompt_manager_tool import PromptManagerTool
from chat_tools.recommendation_tool import RecommendationTool
from chat_tools.time_context_tool import TimeContextTool


def build_tool_registry() -> Dict[str, ChatTool]:
    tools = [
        PromptManagerTool(),
        RecommendationTool(),
        MoodUpdateTool(),
        TimeContextTool(),
    ]
    return {t.spec.name: t for t in tools}

