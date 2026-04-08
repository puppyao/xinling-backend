from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib import error, parse, request

from music_provider import MusicProvider
from playlist_sync import PlaylistSyncService


class ApiMusicProvider(MusicProvider):
    """Fetch recommendation/category data from upstream API service."""

    def __init__(self, base_url: str, *, sync_service: PlaylistSyncService | None = None):
        self.base_url = base_url.rstrip("/")
        self.sync_service = sync_service

    def get_recommendations(self, *, user_id: str, emotion: str) -> List[Dict[str, Any]]:
        _ = user_id
        if self.sync_service:
            try:
                self.sync_service.sync_if_due()
                cached = self.sync_service.get_recommendations(emotion=emotion, limit=30)
                if cached:
                    return cached
            except Exception:
                pass
        query = parse.urlencode({"emotion": emotion, "userId": user_id})
        url = f"{self.base_url}/api/music/recommend?{query}"
        payload = self._get_json(url)
        data = payload.get("data") if isinstance(payload, dict) else None
        music_list = data.get("musicList") if isinstance(data, dict) else None
        return music_list if isinstance(music_list, list) else []

    def get_categories(self, *, user_id: str) -> List[Dict[str, Any]]:
        query = parse.urlencode({"userId": user_id})
        url = f"{self.base_url}/api/music/categories?{query}"
        payload = self._get_json(url)
        data = payload.get("data") if isinstance(payload, dict) else None
        categories = data.get("categories") if isinstance(data, dict) else None
        return categories if isinstance(categories, list) else []

    def play_music(self, *, user_id: str, music_id: str) -> Dict[str, Any]:
        _ = user_id
        if not music_id.startswith("nm_"):
            raise KeyError("music_not_found")
        song_id = music_id[len("nm_") :].strip()
        if not song_id:
            raise KeyError("music_not_found")
        song = self._fetch_song_detail(song_id)
        if not song:
            raise KeyError("music_not_found")
        return song

    def get_lyrics(self, *, user_id: str, music_id: str) -> Dict[str, Any]:
        _ = user_id
        if not music_id.startswith("nm_"):
            return {"musicId": music_id, "lines": [], "raw": "", "translatedRaw": ""}
        song_id = music_id[len("nm_") :].strip()
        if not song_id:
            return {"musicId": music_id, "lines": [], "raw": "", "translatedRaw": ""}
        payload = self._get_json_no_code_check(f"{self.base_url}/lyric?{parse.urlencode({'id': song_id})}")
        lrc = payload.get("lrc") if isinstance(payload, dict) else None
        tlyric = payload.get("tlyric") if isinstance(payload, dict) else None
        raw = lrc.get("lyric") if isinstance(lrc, dict) else ""
        translated = tlyric.get("lyric") if isinstance(tlyric, dict) else ""
        lines = self._parse_lrc(raw if isinstance(raw, str) else "")
        return {
            "musicId": music_id,
            "lines": lines,
            "raw": raw if isinstance(raw, str) else "",
            "translatedRaw": translated if isinstance(translated, str) else "",
        }

    def _get_json(self, url: str) -> Dict[str, Any]:
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
        except (error.URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError("upstream_request_failed") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("upstream_invalid_json") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("upstream_invalid_payload")
        code = payload.get("code")
        if code not in (0, "0"):
            raise RuntimeError("upstream_business_error")
        return payload

    def _post_json(self, url: str, *, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
        req_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers = {"Content-Type": "application/json", **headers}
        req = request.Request(url, data=req_body, headers=req_headers, method="POST")
        try:
            with request.urlopen(req, timeout=5) as resp:
                content = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            if exc.code == 404:
                raise KeyError("music_not_found") from exc
            raise RuntimeError("upstream_request_failed") from exc
        except (error.URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError("upstream_request_failed") from exc
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("upstream_invalid_json") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("upstream_invalid_payload")
        code = payload.get("code")
        if code == 1002:
            raise KeyError("music_not_found")
        if code not in (0, "0"):
            raise RuntimeError("upstream_business_error")
        return payload

    def _fetch_song_detail(self, song_id: str) -> Dict[str, Any]:
        detail_url = f"{self.base_url}/song/detail?{parse.urlencode({'ids': song_id})}"
        detail = self._get_json_no_code_check(detail_url)
        songs = detail.get("songs")
        if not isinstance(songs, list) or not songs:
            raise KeyError("music_not_found")
        first = songs[0] if isinstance(songs[0], dict) else {}
        title = str(first.get("name") or "").strip() or "未知歌曲"
        artists = first.get("ar") if isinstance(first.get("ar"), list) else []
        artist = " / ".join(
            str(a.get("name") or "").strip() for a in artists if isinstance(a, dict)
        ).strip() or "未知歌手"
        cover = ""
        al = first.get("al")
        if isinstance(al, dict):
            cover = str(al.get("picUrl") or "")
        duration_ms = int(first.get("dt") or 0)
        duration = max(0, duration_ms // 1000)

        url_api = f"{self.base_url}/song/url/v1?{parse.urlencode({'id': song_id, 'level': 'standard'})}"
        url_payload = self._get_json_no_code_check(url_api)
        data = url_payload.get("data")
        audio_url = ""
        if isinstance(data, list) and data:
            row = data[0] if isinstance(data[0], dict) else {}
            audio_url = str(row.get("url") or "")
        if not audio_url:
            raise RuntimeError("upstream_audio_unavailable")
        return {
            "id": f"nm_{song_id}",
            "title": title,
            "artist": artist,
            "coverUrl": cover,
            "audioUrl": audio_url,
            "duration": duration,
        }

    def _get_json_no_code_check(self, url: str) -> Dict[str, Any]:
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=8) as resp:
                body = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            if exc.code == 404:
                raise KeyError("music_not_found") from exc
            raise RuntimeError("upstream_request_failed") from exc
        except (error.URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError("upstream_request_failed") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("upstream_invalid_json") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("upstream_invalid_payload")
        return payload

    def _normalize_prefixed_ids(self, data: Dict[str, Any]) -> None:
        current = data.get("currentMusic")
        if isinstance(current, dict):
            cid = current.get("id")
            if isinstance(cid, str) and not cid.startswith("nm_"):
                current["id"] = f"nm_{cid}"
        playlist = data.get("playlist")
        if isinstance(playlist, list):
            for item in playlist:
                if not isinstance(item, dict):
                    continue
                iid = item.get("id")
                if isinstance(iid, str) and not iid.startswith("nm_"):
                    item["id"] = f"nm_{iid}"

    def _parse_lrc(self, text: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not text:
            return out
        for line in text.splitlines():
            tags = re.findall(r"\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]", line)
            lyric = re.sub(r"\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]", "", line).strip()
            if not tags or not lyric:
                continue
            for mm, ss, ms in tags:
                try:
                    minute = int(mm)
                    second = int(ss)
                    millis = int((ms or "0").ljust(3, "0")[:3])
                    time_sec = minute * 60 + second + millis / 1000.0
                    out.append({"time": time_sec, "text": lyric})
                except Exception:
                    continue
        out.sort(key=lambda x: float(x.get("time", 0.0)))
        return out
