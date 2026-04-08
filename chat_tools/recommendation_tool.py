from __future__ import annotations

import re
from typing import Any, Dict

from chat_music_preview import get_recommendation_preview_for_emotion
from chat_tools.base import ToolContext, ToolSpec


_MUSIC_RE = re.compile(r"(歌|音乐|推荐|听什么|song|music)", re.I)


class RecommendationTool:
    spec = ToolSpec(
        name="recommendation_tool",
        version="1.0.0",
        input_schema={"type": "object", "properties": {"user_text": {"type": "string"}}},
        output_schema={"type": "object"},
        safety={"readonly": True},
    )

    def run(self, context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        text = str(args.get("user_text") or "").strip()
        should = bool(_MUSIC_RE.search(text))
        preview = get_recommendation_preview_for_emotion(context.emotion_tag) if should else None
        return {
            "ok": True,
            "data": {
                "should_recommend": bool(preview) if should else False,
                "song_candidate": preview,
                "reason": "keyword_triggered" if should else "no_music_intent",
            },
            "trace": "recommendation",
            "confidence": 0.85 if should else 0.7,
        }

