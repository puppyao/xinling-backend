from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib import error, parse, request


EMOTIONS = ("happy", "anxious", "sad", "calm", "angry")
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "emotion_playlists.json"
CACHE_PATH = Path(__file__).resolve().parent / "config" / "emotion_tracks_cache.json"


class PlaylistSyncService:
    def __init__(self, *, base_url: str, sync_interval_days: int = 30):
        self.base_url = base_url.rstrip("/")
        self.sync_interval_days = max(1, int(sync_interval_days))
        self._lock = threading.RLock()
        self._started = False

    def start_background(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        t = threading.Thread(target=self._loop, daemon=True, name="playlist-sync-loop")
        t.start()

    def _loop(self) -> None:
        while True:
            try:
                self.sync_if_due()
            except Exception:
                pass
            # every hour check once whether monthly sync is due
            time.sleep(3600)

    def sync_if_due(self) -> Dict[str, Any]:
        with self._lock:
            cache = self._load_cache()
            last_sync_ts = int(cache.get("meta", {}).get("lastSyncTs", 0) or 0)
            now = int(time.time())
            interval_secs = self.sync_interval_days * 24 * 3600
            if last_sync_ts > 0 and now - last_sync_ts < interval_secs:
                return cache
        return self.sync_now()

    def sync_now(self) -> Dict[str, Any]:
        with self._lock:
            config = self._load_config()
            data: Dict[str, List[Dict[str, Any]]] = {emotion: [] for emotion in EMOTIONS}
            for emotion in EMOTIONS:
                playlist_ids = config.get(emotion, [])
                merged: List[Dict[str, Any]] = []
                seen_ids: set[str] = set()
                for pid in playlist_ids:
                    tracks = self._fetch_playlist_tracks(pid)
                    for t in tracks:
                        tid = str(t.get("id") or "").strip()
                        if not tid or tid in seen_ids:
                            continue
                        seen_ids.add(tid)
                        row = dict(t)
                        row["_sourcePlaylistId"] = str(pid)
                        merged.append(row)
                data[emotion] = merged[:100]
            cache = {
                "meta": {
                    "lastSyncTs": int(time.time()),
                    "syncIntervalDays": self.sync_interval_days,
                },
                "data": data,
            }
            self._save_cache(cache)
            return cache

    def get_recommendations(self, emotion: str, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            cache = self._load_cache()
            data = cache.get("data", {})
            tracks = data.get(emotion, []) if isinstance(data, dict) else []
            if not isinstance(tracks, list):
                return []
            n = max(1, int(limit))
            if len(tracks) <= n:
                out = list(tracks)
                random.shuffle(out)
                return [self._strip_internal_fields(x) for x in out]

            by_playlist: Dict[str, List[Dict[str, Any]]] = {}
            for row in tracks:
                if not isinstance(row, dict):
                    continue
                pid = str(row.get("_sourcePlaylistId") or "")
                by_playlist.setdefault(pid, []).append(row)

            picked: List[Dict[str, Any]] = []
            picked_ids: set[str] = set()

            # 第一轮：每个歌单尽量至少取 1 首
            playlist_keys = list(by_playlist.keys())
            random.shuffle(playlist_keys)
            for pid in playlist_keys:
                pool = by_playlist.get(pid, [])
                if not pool:
                    continue
                one = random.choice(pool)
                sid = str(one.get("id") or "")
                if sid and sid not in picked_ids:
                    picked.append(one)
                    picked_ids.add(sid)
                if len(picked) >= n:
                    break

            # 第二轮：从剩余池随机补齐
            if len(picked) < n:
                remaining = []
                for row in tracks:
                    if not isinstance(row, dict):
                        continue
                    sid = str(row.get("id") or "")
                    if sid and sid not in picked_ids:
                        remaining.append(row)
                random.shuffle(remaining)
                need = n - len(picked)
                picked.extend(remaining[:need])

            return [self._strip_internal_fields(x) for x in picked[:n]]

    def _strip_internal_fields(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        out.pop("_sourcePlaylistId", None)
        return out

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            cache = self._load_cache()
            meta = cache.get("meta", {}) if isinstance(cache, dict) else {}
            data = cache.get("data", {}) if isinstance(cache, dict) else {}
            counts = {}
            for emotion in EMOTIONS:
                v = data.get(emotion, []) if isinstance(data, dict) else []
                counts[emotion] = len(v) if isinstance(v, list) else 0
            return {
                "lastSyncTs": int(meta.get("lastSyncTs", 0) or 0),
                "syncIntervalDays": self.sync_interval_days,
                "counts": counts,
            }

    def get_sources_for_track_ids(self, *, emotion: str, track_ids: List[str]) -> Dict[str, str]:
        with self._lock:
            cache = self._load_cache()
            data = cache.get("data", {})
            tracks = data.get(emotion, []) if isinstance(data, dict) else []
            if not isinstance(tracks, list):
                return {}
            wanted = {str(tid).strip() for tid in track_ids if str(tid).strip()}
            if not wanted:
                return {}
            out: Dict[str, str] = {}
            for row in tracks:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("id") or "").strip()
                if not sid or sid not in wanted:
                    continue
                source = str(row.get("_sourcePlaylistId") or "").strip()
                if source:
                    out[sid] = source
            return out

    def _fetch_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        q = parse.urlencode({"id": playlist_id})
        url = f"{self.base_url}/playlist/track/all?{q}"
        payload = self._get_json(url)
        songs = payload.get("songs")
        if not isinstance(songs, list):
            return []
        result: List[Dict[str, Any]] = []
        for song in songs:
            mapped = self._map_song(song)
            if mapped:
                result.append(mapped)
        return result

    def _map_song(self, song: Dict[str, Any]) -> Dict[str, Any] | None:
        if not isinstance(song, dict):
            return None
        sid = song.get("id")
        if sid is None:
            return None
        title = str(song.get("name") or "").strip() or "未知歌曲"
        artists = song.get("ar") if isinstance(song.get("ar"), list) else []
        artist = " / ".join(str(a.get("name") or "").strip() for a in artists if isinstance(a, dict)).strip() or "未知歌手"
        pic_url = ""
        al = song.get("al")
        if isinstance(al, dict):
            pic_url = str(al.get("picUrl") or "")
        duration_ms = int(song.get("dt") or 0)
        duration = max(0, duration_ms // 1000)
        return {
            "id": f"nm_{sid}",
            "title": title,
            "artist": artist,
            "coverUrl": pic_url,
            "audioUrl": "",
            "duration": duration,
        }

    def _get_json(self, url: str) -> Dict[str, Any]:
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8")
        except (error.URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError("playlist_sync_upstream_failed") from exc
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("playlist_sync_invalid_json") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("playlist_sync_invalid_payload")
        return payload

    def _load_config(self) -> Dict[str, List[str]]:
        if not CONFIG_PATH.exists():
            return {e: [] for e in EMOTIONS}
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {e: [] for e in EMOTIONS}
        out = {e: [] for e in EMOTIONS}
        if not isinstance(raw, dict):
            return out
        for e in EMOTIONS:
            values = raw.get(e, [])
            if isinstance(values, list):
                out[e] = [str(v).strip() for v in values if str(v).strip().isdigit()]
        return out

    def _load_cache(self) -> Dict[str, Any]:
        if not CACHE_PATH.exists():
            return {"meta": {"lastSyncTs": 0}, "data": {e: [] for e in EMOTIONS}}
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {"meta": {"lastSyncTs": 0}, "data": {e: [] for e in EMOTIONS}}

    def _save_cache(self, payload: Dict[str, Any]) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
