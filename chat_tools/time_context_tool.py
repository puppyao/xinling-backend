from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from chat_tools.base import ToolContext, ToolSpec


class TimeContextTool:
    spec = ToolSpec(
        name="time_context_tool",
        version="1.0.0",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        safety={"readonly": True},
    )

    def run(self, context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        _ = args
        dt = datetime.now()
        h = dt.hour
        if 0 <= h < 5:
            seg, greet = "late_night", "夜深了"
        elif h < 12:
            seg, greet = "morning", "早上好"
        elif h < 18:
            seg, greet = "afternoon", "下午好"
        else:
            seg, greet = "evening", "晚上好"
        return {
            "ok": True,
            "data": {"time_segment": seg, "greeting": greet, "now_iso": context.now_iso},
            "trace": "time_context",
            "confidence": 1.0,
        }

