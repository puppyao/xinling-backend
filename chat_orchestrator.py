from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from chat_music_preview import ensure_reply_mentions_preview_track
from chat_tools.base import ToolContext
from chat_tools.registry import build_tool_registry
from config import Config
from database import get_treehole_messages


def _call_llm(messages: List[Dict[str, str]], *, temperature: float = 0.3) -> str:
    api_key = str(os.getenv("OPENAI_API_KEY") or Config.OPENAI_API_KEY or "").strip()
    base_url = str(os.getenv("OPENAI_BASE_URL") or Config.OPENAI_BASE_URL or "").strip().rstrip("/")
    if not api_key:
        return "AI服务未配置：缺少 OPENAI_API_KEY。"
    if not base_url:
        return "AI服务未配置：缺少 OPENAI_BASE_URL。"
    model = str(os.getenv("OPENAI_MODEL") or Config.OPENAI_MODEL or "").strip()
    if not model:
        model = "deepseek-chat" if "deepseek" in base_url.lower() else "gpt-4o-mini"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": max(0.0, min(2.0, float(temperature))),
        "max_tokens": 500,
    }
    try:
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception:
        return "我刚刚有点走神了，网络恢复后我们继续聊，好吗？"


def _safe_json(raw: str) -> Dict[str, Any]:
    s = str(raw or "").strip()
    if not s:
        return {}
    if s.startswith("```"):
        s = s.strip("`")
        s = s.replace("json", "", 1).strip()
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _default_plan() -> Dict[str, Any]:
    return {
        "tools": [
            {"name": "time_context_tool", "args": {}},
            {"name": "recommendation_tool", "args": {}},
            {"name": "mood_update_tool", "args": {}},
        ],
        "reply_strategy": {"style": "supportive"},
    }


def run_turn(*, user_id: str, content: str, emotion_tag: str) -> Dict[str, Any]:
    registry = build_tool_registry()
    now_iso = datetime.now().replace(microsecond=0).isoformat()
    recent_memory = get_treehole_messages(user_id=user_id, limit=40)
    ctx = ToolContext(
        user_id=user_id,
        emotion_tag=str(emotion_tag or "").strip().lower(),
        now_iso=now_iso,
        conversation_window=recent_memory[-8:],
        recent_memory=recent_memory,
        confidence_budget=1.0,
    )

    planner_prompt = registry["prompt_manager_tool"].run(ctx, {"task": "planner"}).get("data", {}).get("prompt", "")
    planner_messages = [
        {"role": "system", "content": planner_prompt},
        {
            "role": "user",
            "content": (
                f"用户消息: {content}\n"
                f"情绪标签: {emotion_tag}\n"
                "请只输出 JSON，格式: "
                '{"tools":[{"name":"time_context_tool","args":{}},{"name":"recommendation_tool","args":{"user_text":"..."}},{"name":"mood_update_tool","args":{"user_text":"..."}}],"reply_strategy":{"tone":"warm"}}'
            ),
        },
    ]
    planner_raw = _call_llm(planner_messages, temperature=0.1)
    plan = _safe_json(planner_raw) or _default_plan()
    tools_plan = plan.get("tools") if isinstance(plan.get("tools"), list) else _default_plan()["tools"]

    tool_results: Dict[str, Dict[str, Any]] = {}
    for call in tools_plan:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        if not name or name not in registry:
            continue
        args = call.get("args")
        if not isinstance(args, dict):
            args = {}
        if name in ("recommendation_tool", "mood_update_tool") and "user_text" not in args:
            args["user_text"] = content
        try:
            tool_results[name] = registry[name].run(ctx, args)
        except Exception:
            tool_results[name] = {"ok": False, "data": {}, "trace": "tool_error", "confidence": 0.0}

    rec_data = tool_results.get("recommendation_tool", {}).get("data", {})
    song = rec_data.get("song_candidate") if isinstance(rec_data, dict) else None
    should_rec = bool(rec_data.get("should_recommend")) if isinstance(rec_data, dict) else False
    time_data = tool_results.get("time_context_tool", {}).get("data", {})
    greeting = str(time_data.get("greeting") or "").strip() if isinstance(time_data, dict) else ""

    composer_prompt = registry["prompt_manager_tool"].run(ctx, {"task": "composer"}).get("data", {}).get("prompt", "")
    song_hint = ""
    if should_rec and isinstance(song, dict):
        t = str(song.get("title") or "").strip()
        a = str(song.get("artist") or "").strip()
        if t:
            song_hint = f"本轮若推荐歌曲，仅可推荐：{t}" + (f" - {a}" if a else "")

    compose_messages = [
        {"role": "system", "content": composer_prompt},
        {
            "role": "user",
            "content": (
                f"时间问候: {greeting}\n"
                f"{song_hint}\n"
                f"用户原话: {content}\n"
                "请直接输出对用户说的话。"
            ),
        },
    ]
    ai_reply = _call_llm(compose_messages, temperature=0.25)
    ai_reply = ensure_reply_mentions_preview_track(ai_reply, song if should_rec else None)

    music_tip = None
    if should_rec and isinstance(song, dict):
        t = str(song.get("title") or "").strip()
        a = str(song.get("artist") or "").strip()
        if t:
            music_tip = f"{t} — {a}" if a else t

    return {
        "aiReply": ai_reply,
        "suggestMusic": song if should_rec else None,
        "musicTip": music_tip,
        "agentTrace": {"plan": plan, "toolResults": tool_results},
    }

