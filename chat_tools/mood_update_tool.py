from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from chat_tools.base import ToolContext, ToolSpec
from database import add_mood, get_latest_mood_on_date


_EMO_KEYWORDS = {
    "happy": ["开心", "高兴", "不错", "愉快", "满足"],
    "sad": ["难过", "伤心", "低落", "想哭", "压抑"],
    "anxious": ["焦虑", "紧张", "慌", "担心", "不安", "烦躁"],
    "angry": ["生气", "愤怒", "火大", "气死", "烦死"],
    "calm": ["平静", "放松", "稳定", "安心", "还行"],
}


class MoodUpdateTool:
    spec = ToolSpec(
        name="mood_update_tool",
        version="1.0.0",
        input_schema={"type": "object", "properties": {"user_text": {"type": "string"}}},
        output_schema={"type": "object"},
        safety={"only_today": True, "requires_high_confidence": True},
    )

    def _infer_emotion(self, text: str) -> Tuple[str | None, float]:
        raw = str(text or "").strip()
        if not raw:
            return None, 0.0
        best = (None, 0.0)
        for emo, words in _EMO_KEYWORDS.items():
            score = 0.0
            for w in words:
                if w in raw:
                    score += 1.0
            if score > best[1]:
                best = (emo, score)
        if not best[0]:
            return None, 0.0
        conf = min(0.95, 0.55 + 0.15 * best[1])
        return best[0], conf

    def _decay_merge(self, user_text: str, memory: List[Dict[str, Any]]) -> Tuple[str | None, float]:
        signals: List[Tuple[str, float]] = []
        now_emo, now_conf = self._infer_emotion(user_text)
        if now_emo:
            signals.append((now_emo, now_conf * 1.0))
        # 最近 8 条用户发言，按轮次衰减
        user_msgs = [m for m in memory if isinstance(m, dict) and m.get("type") == "user"]
        recent = user_msgs[-8:]
        for idx, m in enumerate(reversed(recent), start=1):
            emo, conf = self._infer_emotion(str(m.get("text") or ""))
            if not emo:
                continue
            weight = 0.78 ** idx
            signals.append((emo, conf * weight))
        if not signals:
            return None, 0.0
        agg: Dict[str, float] = {}
        for emo, score in signals:
            agg[emo] = agg.get(emo, 0.0) + score
        emo = max(agg, key=agg.get)
        total = sum(agg.values()) or 1.0
        confidence = min(0.99, agg[emo] / total + 0.15)
        return emo, confidence

    def run(self, context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        text = str(args.get("user_text") or "").strip()
        emo, conf = self._decay_merge(text, context.recent_memory or [])
        if not emo or conf < 0.78:
            return {
                "ok": True,
                "data": {"should_update": False, "emotion": None, "confidence": conf},
                "trace": "confidence_not_enough",
                "confidence": conf,
            }
        today = datetime.now().strftime("%Y-%m-%d")
        latest_today = get_latest_mood_on_date(user_id=context.user_id, date=today)
        if latest_today and str(latest_today.get("emotion") or "") == emo:
            return {
                "ok": True,
                "data": {"should_update": False, "emotion": emo, "confidence": conf},
                "trace": "same_as_today",
                "confidence": conf,
            }
        add_mood(user_id=context.user_id, date=today, emotion=emo, note="auto_by_treehole")
        return {
            "ok": True,
            "data": {"should_update": True, "emotion": emo, "confidence": conf, "date": today},
            "trace": "updated_today_mood",
            "confidence": conf,
        }

