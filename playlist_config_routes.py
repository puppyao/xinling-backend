from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, request

from music_routes import playlist_sync_service
from utils import make_response, require_user_id


playlist_config_bp = Blueprint("playlist_config_bp", __name__)

_EMOTIONS = ("happy", "anxious", "sad", "calm", "angry")
_PLAYLIST_ID_RE = re.compile(r"^\d+$")
_PLAYLIST_URL_RE = re.compile(r"[?&]id=(\d+)")
_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "emotion_playlists.json"


def _default_config() -> Dict[str, List[str]]:
    return {emotion: [] for emotion in _EMOTIONS}


def _load_config() -> Dict[str, List[str]]:
    if not _CONFIG_PATH.exists():
        return _default_config()
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_config()
    if not isinstance(raw, dict):
        return _default_config()
    data = _default_config()
    for emotion in _EMOTIONS:
        values = raw.get(emotion)
        if isinstance(values, list):
            normalized = [str(v).strip() for v in values if str(v).strip().isdigit()]
            data[emotion] = list(dict.fromkeys(normalized))
    return data


def _save_config(data: Dict[str, List[str]]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_playlist_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _PLAYLIST_ID_RE.fullmatch(text):
        return text
    m = _PLAYLIST_URL_RE.search(text)
    if m:
        return m.group(1)
    return None


@playlist_config_bp.get("/api/music/playlists/config")
@require_user_id
def get_playlist_config(*, user_id: str):
    _ = user_id
    return make_response(code=0, message="ok", data={"emotionPlaylists": _load_config()}, http_status=200)


@playlist_config_bp.get("/api/music/playlists/sync-status")
@require_user_id
def get_sync_status(*, user_id: str):
    _ = user_id
    return make_response(code=0, message="ok", data=playlist_sync_service.get_status(), http_status=200)


@playlist_config_bp.post("/api/music/playlists/import")
@require_user_id
def import_playlist(*, user_id: str):
    _ = user_id
    body = request.get_json(silent=True) or {}
    emotion = body.get("emotion")
    source = body.get("playlist")
    if emotion not in _EMOTIONS:
        return make_response(code=1001, message="emotion必须是 happy/anxious/sad/calm/angry", data=None, http_status=400)
    playlist_id = _extract_playlist_id(source)
    if not playlist_id:
        return make_response(code=1001, message="playlist必须是歌单ID或包含?id=xxx的URL", data=None, http_status=400)

    config = _load_config()
    old = config.get(emotion, [])
    if playlist_id not in old:
        old.append(playlist_id)
    config[emotion] = old
    _save_config(config)
    return make_response(
        code=0,
        message="ok",
        data={"emotion": emotion, "importedPlaylistId": playlist_id, "emotionPlaylists": config},
        http_status=200,
    )


@playlist_config_bp.post("/api/music/playlists/sync")
@require_user_id
def sync_playlists(*, user_id: str):
    _ = user_id
    result = playlist_sync_service.sync_now()
    return make_response(code=0, message="ok", data=result.get("meta", {}), http_status=200)
