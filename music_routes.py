from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from flask import Blueprint, request

from config import config
from music_player import ALLOWED_EMOTIONS, ALLOWED_PLAY_MODES, MusicPlayer
from music_provider_api import ApiMusicProvider
from music_provider_local import LocalMusicProvider
from music_provider_router import MusicProviderRouter
from playlist_sync import PlaylistSyncService
from utils import make_response, require_user_id


music_bp = Blueprint("music_bp", __name__)
_http_logger = logging.getLogger("app.http")
_is_prod = os.getenv("APP_ENV", "development").strip().lower() == "production"


# 单例播放器核心：后端只维护一份音乐库索引与播放状态。
# 注意：不要在 import 时做耗时初始化；由 app 启动流程显式调用 init_music_library()。
player = MusicPlayer(config.musicLibraryPath)


def init_music_library() -> None:
    """在应用启动时调用：扫描音乐库并建立索引。"""
    player.load_music_library()


_local_provider = LocalMusicProvider(player)
playlist_sync_service = PlaylistSyncService(
    base_url=config.musicApiBaseUrl,
    sync_interval_days=config.playlistSyncIntervalDays,
)
_api_provider = ApiMusicProvider(config.musicApiBaseUrl, sync_service=playlist_sync_service)
provider = MusicProviderRouter(
    local_provider=_local_provider,
    api_provider=_api_provider,
    mode=config.musicProviderMode,
)


def _get_json() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _parse_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except Exception:
            return None
    return None


@music_bp.get("/api/music/recommend")
@require_user_id
def recommend_music(*, user_id: str):
    emotion = request.args.get("emotion")
    if not emotion:
        return make_response(code=1001, message="缺少情绪参数emotion", data=None, http_status=400)
    if emotion not in ALLOWED_EMOTIONS:
        return make_response(code=1001, message="emotion值不在允许范围内", data=None, http_status=400)

    try:
        music_list = provider.get_recommendations(user_id=user_id, emotion=emotion)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)

    if not _is_prod:
        try:
            track_ids = [str(row.get("id") or "").strip() for row in music_list if isinstance(row, dict)]
            track_ids = [x for x in track_ids if x]
            source_map = playlist_sync_service.get_sources_for_track_ids(emotion=emotion, track_ids=track_ids)
            source_ids = sorted({v for v in source_map.values() if v})
            _http_logger.info(
                "RECOMMEND_DEBUG emotion=%s total=%s sourcePlaylistIds=%s",
                emotion,
                len(music_list),
                source_ids,
            )
        except Exception:
            pass
    return make_response(
        code=0,
        message="ok",
        data={"emotion": emotion, "total": len(music_list), "musicList": music_list},
        http_status=200,
    )


@music_bp.get("/api/music/status")
@require_user_id
def get_status(*, user_id: str):
    return make_response(code=0, message="ok", data=player.get_play_status(user_id), http_status=200)


@music_bp.post("/api/music/toggle-play")
@require_user_id
def toggle_play(*, user_id: str):
    # 文档 request body 为 {}，此处不强制字段，仅维持一致行为
    return make_response(code=0, message="ok", data=player.toggle_play(user_id), http_status=200)


@music_bp.post("/api/music/play")
@require_user_id
def play_music(*, user_id: str):
    body = _get_json()
    music_id = body.get("musicId")
    if not music_id or not isinstance(music_id, str):
        return make_response(code=1001, message="缺少必要参数 musicId", data=None, http_status=400)

    try:
        if music_id.startswith("nm_"):
            song = _api_provider.play_music(user_id=user_id, music_id=music_id)
            st = player.get_or_create_user_state(user_id)
            st["playState"] = "playing"
            st["currentTime"] = 0
            st["duration"] = int(song.get("duration", 0) or 0)
            st["currentMusic"] = song
            st["playlist"] = [
                {
                    "id": song.get("id", ""),
                    "title": song.get("title", ""),
                    "artist": song.get("artist", ""),
                    "duration": int(song.get("duration", 0) or 0),
                }
            ]
            # 重置内部计时，避免切歌后沿用上一首 currentTime
            player._timing[user_id] = {"baseOffset": 0.0, "startedAt": time.monotonic()}  # type: ignore[attr-defined]
            data = player.get_play_status(user_id)
        else:
            data = provider.play_music(user_id=user_id, music_id=music_id)
        return make_response(code=0, message="ok", data=data, http_status=200)
    except KeyError as e:
        if str(e) == "'music_not_found'":
            return make_response(code=1002, message="音乐不存在", data=None, http_status=404)
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)
    except RuntimeError as e:
        if str(e) == "upstream_play_failed":
            return make_response(code=1004, message="上游播放服务不可用", data=None, http_status=502)
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@music_bp.post("/api/music/previous")
@require_user_id
def previous_track(*, user_id: str):
    try:
        data = player.previous_track(user_id)
        return make_response(code=0, message="ok", data=data, http_status=200)
    except KeyError as e:
        if str(e) == "'playlist_empty'":
            return make_response(code=1002, message="播放列表为空", data=None, http_status=404)
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@music_bp.post("/api/music/next")
@require_user_id
def next_track(*, user_id: str):
    try:
        data = player.next_track(user_id)
        return make_response(code=0, message="ok", data=data, http_status=200)
    except KeyError as e:
        if str(e) == "'playlist_empty'":
            return make_response(code=1002, message="播放列表为空", data=None, http_status=404)
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@music_bp.post("/api/music/seek")
@require_user_id
def seek(*, user_id: str):
    body = _get_json()
    pos = _parse_number(body.get("position"))
    if pos is None:
        return make_response(code=1001, message="缺少必要参数 position", data=None, http_status=400)

    st = player.get_or_create_user_state(user_id)
    dur = float(st.get("duration", 0) or 0)
    if dur <= 0:
        # 没有正在播放的曲目，保持状态返回
        return make_response(code=0, message="ok", data=player.get_play_status(user_id), http_status=200)

    if pos < 0 or pos > dur:
        return make_response(code=1001, message=f"position必须在0-{int(dur)}之间", data=None, http_status=400)

    return make_response(code=0, message="ok", data=player.seek(user_id, pos), http_status=200)


@music_bp.post("/api/music/volume")
@require_user_id
def set_volume(*, user_id: str):
    body = _get_json()
    v = _parse_number(body.get("volume"))
    if v is None:
        return make_response(code=1001, message="缺少必要参数 volume", data=None, http_status=400)
    if v < 0.0 or v > 1.0:
        return make_response(code=1001, message="volume必须在0.0-1.0之间", data=None, http_status=400)

    return make_response(code=0, message="ok", data=player.set_volume(user_id, v), http_status=200)


@music_bp.post("/api/music/play-mode")
@require_user_id
def set_play_mode(*, user_id: str):
    body = _get_json()
    mode = body.get("mode")
    if not mode or not isinstance(mode, str):
        return make_response(code=1001, message="缺少必要参数 mode", data=None, http_status=400)
    if mode not in ALLOWED_PLAY_MODES:
        return make_response(code=1001, message="mode必须是 listLoop/singleLoop/random/sequence 之一", data=None, http_status=400)

    return make_response(code=0, message="ok", data=player.set_play_mode(user_id, mode), http_status=200)


@music_bp.get("/api/music/categories")
@require_user_id
def get_categories(*, user_id: str):
    try:
        categories = provider.get_categories(user_id=user_id)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)
    return make_response(code=0, message="ok", data={"categories": categories}, http_status=200)


@music_bp.get("/api/music/lyrics")
@require_user_id
def get_lyrics(*, user_id: str):
    music_id = request.args.get("musicId")
    if not music_id:
        return make_response(code=1001, message="缺少必要参数 musicId", data=None, http_status=400)
    try:
        data = provider.get_lyrics(user_id=user_id, music_id=str(music_id))
        return make_response(code=0, message="ok", data=data, http_status=200)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)

