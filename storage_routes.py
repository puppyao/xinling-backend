from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, Optional

from flask import Blueprint, request

from database import (
    add_favorite,
    add_mood,
    add_questionnaire,
    add_recent,
    delete_mood,
    get_favorites,
    get_latest_questionnaire,
    get_moods,
    get_preferences,
    get_recent,
    remove_favorite,
    save_preferences,
    get_treehole_messages,
    replace_treehole_messages,
    clear_treehole_messages,
)
from utils import make_response, require_user_id


storage_bp = Blueprint("storage_bp", __name__)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_EMOTION_SCORE = {"happy": 4, "calm": 3, "angry": 2, "anxious": 1, "sad": 0}
_ALLOWED_EMOTIONS = ("happy", "sad", "anxious", "calm", "angry")


def _get_json() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _parse_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _is_valid_date(s: Optional[str]) -> bool:
    return bool(s) and bool(_DATE_RE.match(str(s)))


# -----------------------------
# 1. 心情记录
# -----------------------------
@storage_bp.post("/api/moods")
@require_user_id
def create_mood(*, user_id: str):
    body = _get_json()
    date = body.get("date")
    emotion = body.get("emotion")
    note = body.get("note")

    if not date:
        return make_response(code=1001, message="缺少必要参数 date", data=None, http_status=400)
    if not emotion:
        return make_response(code=1001, message="缺少必要参数 emotion", data=None, http_status=400)
    if not isinstance(date, str) or not _is_valid_date(date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)
    if not isinstance(emotion, str) or emotion not in _ALLOWED_EMOTIONS:
        return make_response(code=1001, message="emotion值不在允许范围内", data=None, http_status=400)
    if note is not None and not isinstance(note, str):
        return make_response(code=1001, message="note必须是字符串", data=None, http_status=400)

    mood_id = add_mood(user_id=user_id, date=date, emotion=emotion, note=note)
    return make_response(code=0, message="ok", data={"id": mood_id}, http_status=200)


@storage_bp.get("/api/moods")
@require_user_id
def list_moods(*, user_id: str):
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    if start_date and not _is_valid_date(start_date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)
    if end_date and not _is_valid_date(end_date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)

    moods = get_moods(user_id=user_id, start_date=start_date, end_date=end_date)
    return make_response(code=0, message="ok", data={"moods": moods}, http_status=200)


@storage_bp.delete("/api/moods/<int:mood_id>")
@require_user_id
def remove_mood(*, user_id: str, mood_id: int):
    ok = delete_mood(user_id=user_id, mood_id=mood_id)
    if not ok:
        return make_response(code=1002, message="心情记录不存在", data=None, http_status=404)
    return make_response(code=0, message="ok", data=None, http_status=200)


# -----------------------------
# 2. 收藏音乐
# -----------------------------
@storage_bp.post("/api/favorites")
@require_user_id
def create_favorite(*, user_id: str):
    body = _get_json()
    music_id = body.get("musicId")
    title = body.get("title")
    artist = body.get("artist")
    cover_url = body.get("coverUrl")

    for field in ("musicId", "title", "artist", "coverUrl"):
        if not body.get(field):
            return make_response(code=1001, message=f"缺少必要参数 {field}", data=None, http_status=400)
        if not isinstance(body.get(field), str):
            return make_response(code=1001, message=f"{field}必须是字符串", data=None, http_status=400)

    try:
        fav_id = add_favorite(
            user_id=user_id,
            music_id=str(music_id),
            title=str(title),
            artist=str(artist),
            cover_url=str(cover_url),
        )
        return make_response(code=0, message="ok", data={"id": fav_id}, http_status=200)
    except sqlite3.IntegrityError:
        # 统一规则：重复收藏返回 409 + code=1001
        return make_response(code=1001, message="已经收藏过该音乐", data=None, http_status=409)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@storage_bp.get("/api/favorites")
@require_user_id
def list_favorites(*, user_id: str):
    page = _parse_int(request.args.get("page"), 1)
    size = _parse_int(request.args.get("size"), 20)
    favorites, total = get_favorites(user_id=user_id, page=page, size=size)
    return make_response(code=0, message="ok", data={"favorites": favorites, "total": total}, http_status=200)


@storage_bp.delete("/api/favorites/<music_id>")
@require_user_id
def delete_favorite(*, user_id: str, music_id: str):
    try:
        ok = remove_favorite(user_id=user_id, music_id=music_id)
        if not ok:
            return make_response(code=1002, message="收藏记录不存在", data=None, http_status=404)
        return make_response(code=0, message="ok", data=None, http_status=200)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


# -----------------------------
# 3. 最近播放
# -----------------------------
@storage_bp.post("/api/recently-played")
@require_user_id
def create_recent(*, user_id: str):
    body = _get_json()
    for field in ("musicId", "title", "artist", "coverUrl"):
        if not body.get(field):
            # 文档示例 message: "缺少必要参数 musicId"
            return make_response(code=1001, message=f"缺少必要参数 {field}", data=None, http_status=400)
        if not isinstance(body.get(field), str):
            return make_response(code=1001, message=f"{field}必须是字符串", data=None, http_status=400)

    played_at = body.get("playedAt")
    if played_at is not None and not isinstance(played_at, str):
        return make_response(code=1001, message="playedAt必须是字符串", data=None, http_status=400)

    try:
        rid = add_recent(
            user_id=user_id,
            music_id=body["musicId"],
            title=body["title"],
            artist=body["artist"],
            cover_url=body["coverUrl"],
            played_at=played_at,
        )
        return make_response(code=0, message="ok", data={"id": rid}, http_status=200)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@storage_bp.get("/api/recently-played")
@require_user_id
def list_recent(*, user_id: str):
    limit = _parse_int(request.args.get("limit"), 20)
    recent = get_recent(user_id=user_id, limit=limit)
    return make_response(code=0, message="ok", data={"recent": recent}, http_status=200)


# -----------------------------
# 4. 情绪统计
# -----------------------------
@storage_bp.get("/api/stats/emotions/pie")
@require_user_id
def emotion_pie(*, user_id: str):
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    if start_date and not _is_valid_date(start_date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)
    if end_date and not _is_valid_date(end_date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)

    moods = get_moods(user_id=user_id, start_date=start_date, end_date=end_date)
    counts = {e: 0 for e in _ALLOWED_EMOTIONS}
    for m in moods:
        emo = m.get("emotion")
        if emo in counts:
            counts[emo] += 1

    total = sum(counts.values())
    stats = []
    for emo in _ALLOWED_EMOTIONS:
        c = counts[emo]
        pct = round((c * 100.0 / total), 1) if total > 0 else 0.0
        stats.append({"emotion": emo, "count": c, "percentage": pct})

    return make_response(code=0, message="ok", data={"stats": stats}, http_status=200)


@storage_bp.get("/api/stats/emotions/line")
@require_user_id
def emotion_line(*, user_id: str):
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    if start_date and not _is_valid_date(start_date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)
    if end_date and not _is_valid_date(end_date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)

    moods = get_moods(user_id=user_id, start_date=start_date, end_date=end_date)
    # get_moods 默认 date DESC，这里按日期升序更贴近折线图语义与文档示例
    moods_sorted = sorted(moods, key=lambda x: (x.get("date") or "", int(x.get("id") or 0)))
    stats = []
    for m in moods_sorted:
        emo = m.get("emotion")
        if emo not in _EMOTION_SCORE:
            continue
        stats.append(
            {
                "date": m.get("date"),
                "emotion": emo,
                "emotionScore": int(_EMOTION_SCORE[emo]),
            }
        )
    return make_response(code=0, message="ok", data={"stats": stats}, http_status=200)


# -----------------------------
# 5. 问卷
# -----------------------------
@storage_bp.post("/api/questionnaire")
@require_user_id
def create_questionnaire(*, user_id: str):
    body = _get_json()
    date = body.get("date")
    q_type = body.get("type")
    score = body.get("score")
    level = body.get("level")
    details = body.get("details")

    for field in ("date", "type", "score", "level"):
        if body.get(field) is None or body.get(field) == "":
            return make_response(code=1001, message=f"缺少必要参数 {field}", data=None, http_status=400)

    if not isinstance(date, str) or not _is_valid_date(date):
        return make_response(code=1001, message="日期格式错误", data=None, http_status=400)
    if not isinstance(q_type, str) or q_type not in ("SAS", "SDS"):
        return make_response(code=1001, message="type必须是SAS或SDS", data=None, http_status=400)
    try:
        score_int = int(score)
    except Exception:
        return make_response(code=1001, message="score必须是整数", data=None, http_status=400)
    if not isinstance(level, str):
        return make_response(code=1001, message="level必须是字符串", data=None, http_status=400)
    if details is not None and not isinstance(details, dict):
        return make_response(code=1001, message="details必须是对象", data=None, http_status=400)

    qid = add_questionnaire(
        user_id=user_id,
        date=date,
        q_type=q_type,
        score=score_int,
        level=level,
        details=details or {},
    )
    return make_response(code=0, message="ok", data={"id": qid}, http_status=200)


@storage_bp.get("/api/questionnaire/latest")
@require_user_id
def latest_questionnaire(*, user_id: str):
    latest = get_latest_questionnaire(user_id=user_id)
    if not latest:
        return make_response(code=1002, message="该用户暂无问卷记录", data=None, http_status=404)
    return make_response(code=0, message="ok", data=latest, http_status=200)


# -----------------------------
# 6. 用户设置
# -----------------------------
@storage_bp.get("/api/user/preferences")
@require_user_id
def read_preferences(*, user_id: str):
    prefs = get_preferences(user_id=user_id)
    return make_response(code=0, message="ok", data=prefs, http_status=200)


@storage_bp.patch("/api/user/preferences")
@require_user_id
def patch_preferences(*, user_id: str):
    body = _get_json()

    preferred_music_genres = body.get("preferredMusicGenres")
    preferred_healing_methods = body.get("preferredHealingMethods")
    notification_enabled = body.get("notificationEnabled")

    if preferred_music_genres is not None and not isinstance(preferred_music_genres, list):
        return make_response(code=1001, message="preferredMusicGenres 必须是数组", data=None, http_status=400)
    if preferred_healing_methods is not None and not isinstance(preferred_healing_methods, list):
        return make_response(code=1001, message="preferredHealingMethods 必须是数组", data=None, http_status=400)
    if notification_enabled is not None and not isinstance(notification_enabled, bool):
        return make_response(code=1001, message="notificationEnabled 必须是布尔值", data=None, http_status=400)

    prefs = save_preferences(
        user_id=user_id,
        preferred_music_genres=preferred_music_genres,
        preferred_healing_methods=preferred_healing_methods,
        notification_enabled=notification_enabled,
    )
    return make_response(code=0, message="ok", data=prefs, http_status=200)


# -----------------------------
# 7. 情绪树洞历史（按用户隔离）
# -----------------------------
@storage_bp.get("/api/treehole/history")
@require_user_id
def get_treehole_history(*, user_id: str):
    limit = _parse_int(request.args.get("limit"), 300)
    messages = get_treehole_messages(user_id=user_id, limit=limit)
    return make_response(code=0, message="ok", data={"messages": messages}, http_status=200)


@storage_bp.put("/api/treehole/history")
@require_user_id
def put_treehole_history(*, user_id: str):
    body = _get_json()
    messages = body.get("messages")
    if not isinstance(messages, list):
        return make_response(code=1001, message="messages必须是数组", data=None, http_status=400)
    normalized = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        mtype = str(item.get("type") or "").strip()
        if mtype not in ("ai", "user", "music"):
            continue
        row = {
            "type": mtype,
            "text": str(item.get("text") or ""),
        }
        song = item.get("song")
        if isinstance(song, dict):
            row["song"] = song
        normalized.append(row)
    try:
        replace_treehole_messages(user_id=user_id, messages=normalized)
        return make_response(code=0, message="ok", data={"count": len(normalized)}, http_status=200)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@storage_bp.delete("/api/treehole/history")
@require_user_id
def delete_treehole_history(*, user_id: str):
    try:
        clear_treehole_messages(user_id=user_id)
        return make_response(code=0, message="ok", data=None, http_status=200)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)

