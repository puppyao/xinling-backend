"""
情绪树洞聊天：推荐曲目「预览」上下文（与 /api/music/recommend 同源缓存逻辑）。

与 chat_routes / LLM 调用解耦：本模块只负责：
- 按情绪拉一首候选曲目的元数据
- 格式化为可注入 system prompt 的说明文字

不在此处调用大模型、不写 Flask 路由。
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

# 与 music_player / storage 等处的情绪集合对齐
_ALLOWED_EMOTIONS = frozenset({"happy", "sad", "anxious", "angry", "calm"})


def normalize_emotion(emotion: str) -> str:
    e = str(emotion or "").strip().lower()
    return e if e in _ALLOWED_EMOTIONS else "calm"


def get_recommendation_preview_for_emotion(emotion: str) -> Optional[Dict[str, Any]]:
    """
    从歌单同步缓存中随机取一首，作为「本回合可推荐」的元数据。

    与 PlaylistSyncService.get_recommendations 一致，不触发播放、不占用播放器状态。
    若缓存为空或异常则返回 None。
    """
    e = normalize_emotion(emotion)
    try:
        # 延迟导入，避免与 app 启动顺序产生环依赖
        from music_routes import playlist_sync_service
    except Exception:
        return None

    try:
        playlist_sync_service.sync_if_due()
        tracks = playlist_sync_service.get_recommendations(emotion=e, limit=30)
        if not tracks:
            return None
        pick = random.choice(tracks)
        if not isinstance(pick, dict):
            return None
        sid = str(pick.get("id") or "").strip()
        if not sid:
            return None
        title = str(pick.get("title") or "").strip() or "未知歌曲"
        artist = str(pick.get("artist") or "").strip() or "未知歌手"
        duration = int(pick.get("duration") or 0)
        cover = str(pick.get("coverUrl") or pick.get("cover") or "").strip()
        return {
            "id": sid,
            "title": title,
            "artist": artist,
            "duration": duration,
            "coverUrl": cover,
        }
    except Exception:
        return None


def format_music_preview_for_llm(preview: Optional[Dict[str, Any]]) -> str:
    """
    将预览曲目格式化为可追加到 system prompt 的一段说明（中文）。
    preview 为 None 时返回空字符串。
    """
    if not preview or not isinstance(preview, dict):
        return ""
    title = str(preview.get("title") or "").strip()
    artist = str(preview.get("artist") or "").strip()
    duration = int(preview.get("duration") or 0)
    if not title and not artist:
        return ""

    dur_hint = ""
    if duration > 0:
        m, s = duration // 60, duration % 60
        dur_hint = f"约 {m} 分 {s:02d} 秒"

    lines = [
        "【本回合唯一可推荐的曲目（与 App 卡片将展示的一致）】",
        f"歌名（必须原样使用）：{title}",
        f"歌手（必须原样使用）：{artist}",
    ]
    if dur_hint:
        lines.append(f"时长：{dur_hint}")
    lines.extend(
        [
            "",
            "【硬性规则】",
            "1) 当用户请求推歌、问听什么、或你主动提到具体歌名/歌手时：你只能介绍上面这一首歌，"
            "必须使用与「歌名」「歌手」完全一致的用词，不得替换、缩写、翻译为其他名称，不得虚构或提及任何其他歌曲/艺人。",
            "2) 不要编造「我最近听到一首《xxx》」之类与上表不符的内容；若上表有歌名，你的推荐只能围绕该歌名与歌手展开。",
            "3) 不必每句都重复全名，但一旦说出歌名或歌手，必须与上表一致。",
            "4) 不要声称用户已播放；可邀请对方在卡片里试听。",
            "5) 若本回合没有上方曲目信息，则不要推荐具体歌名，只给情绪或风格层面的陪伴。",
        ]
    )
    return "\n".join(lines)


def merge_system_prompt_with_music_preview(
    base_system_prompt: str,
    preview: Optional[Dict[str, Any]],
) -> str:
    """
    将曲目预览块追加到基础 system prompt 末尾。
    """
    block = format_music_preview_for_llm(preview)
    if not block:
        return base_system_prompt
    return f"{base_system_prompt.rstrip()}\n\n{block}\n"


def build_user_message_with_music_constraint(
    user_content: str,
    preview: Optional[Dict[str, Any]],
) -> str:
    """
    把「唯一曲目」约束紧贴在用户原话之前（不少模型对 user 段末尾指令更服从）。
    """
    text = str(user_content or "").strip()
    if not preview or not isinstance(preview, dict):
        return text
    title = str(preview.get("title") or "").strip()
    artist = str(preview.get("artist") or "").strip()
    if not title:
        return text
    artist_line = f"歌手：{artist}\n" if artist else ""
    return (
        "【以下曲目为系统唯一指定，你的回复若提到具体歌名/歌手，必须与之一致，禁止虚构其他歌曲】\n"
        f"歌名：{title}\n"
        f"{artist_line}"
        "---\n"
        f"用户说：{text}"
    )


def ensure_reply_mentions_preview_track(reply: str, preview: Optional[Dict[str, Any]]) -> str:
    """
    若模型仍编造歌名，在回复开头补一行规范推荐语，保证与卡片一致。
    """
    if not reply or not preview or not isinstance(preview, dict):
        return reply
    title = str(preview.get("title") or "").strip()
    artist = str(preview.get("artist") or "").strip()
    if not title:
        return reply
    if title in reply:
        return reply
    lead = f"好呀～推荐你听《{title}》"
    if artist:
        lead += f"，来自{artist}"
    lead += "。"
    body = reply.strip()
    return f"{lead}\n{body}" if body else lead
