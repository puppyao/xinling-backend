from __future__ import annotations

from typing import Any, Dict

from chat_tools.base import ToolContext, ToolSpec


class PromptManagerTool:
    spec = ToolSpec(
        name="prompt_manager_tool",
        version="1.0.0",
        input_schema={"type": "object", "properties": {"task": {"type": "string"}}},
        output_schema={"type": "object"},
        safety={"readonly": True},
    )

    def run(self, context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        _ = context
        task = str(args.get("task") or "").strip().lower()
        if task == "planner":
            return {
                "ok": True,
                "data": {
                    "prompt": (
                        "你是树洞对话编排器。请输出严格 JSON，不要输出额外文本。\n"
                        "字段: tools(list), reply_strategy(object)。\n"
                        "tools 内可用工具: recommendation_tool, mood_update_tool, time_context_tool。\n"
                        "若不需要推歌，recommendation_tool 的 should_recommend 应为 false。"
                    )
                },
                "trace": "planner_prompt",
                "confidence": 1.0,
            }
        if task == "composer":
            return {
                "ok": True,
                "data": {
                    "prompt": (
                        "你是“聆聆”，一个情绪陪伴助手。2-4句，先共情再建议，口语化。"
                        "若有歌曲推荐，仅围绕指定歌曲。可按 time_context 使用问候。"
                    )
                },
                "trace": "composer_prompt",
                "confidence": 1.0,
            }
        return {"ok": True, "data": {"prompt": ""}, "trace": "empty_prompt", "confidence": 1.0}

