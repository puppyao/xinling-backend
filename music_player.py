from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pygame

    _PYGAME_AVAILABLE = True
except Exception:
    # 允许在未安装/不可用 pygame 的环境下导入本模块（例如仅做接口联调）。
    pygame = None  # type: ignore[assignment]
    _PYGAME_AVAILABLE = False


ALLOWED_EMOTIONS = ("anxious", "happy", "sad", "calm", "angry")
ALLOWED_PLAY_STATES = ("playing", "paused", "stopped", "loading")
ALLOWED_PLAY_MODES = ("listLoop", "singleLoop", "random", "sequence")


@dataclass(frozen=True)
class Music:
    """
    与 OpenAPI 文档中 Music 模型保持一致的音乐对象。

    注意：该对象只包含 API 所需字段；真实文件路径由 MusicPlayer 内部单独维护，
    避免把本地绝对路径直接暴露给前端。
    """

    id: str
    title: str
    artist: str
    coverUrl: str
    audioUrl: str
    duration: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "coverUrl": self.coverUrl,
            "audioUrl": self.audioUrl,
            "duration": self.duration,
        }


class MusicPlayer:
    """
    音乐播放核心逻辑（音乐库扫描 + 用户播放状态机 + 播放模式切歌策略）。

    设计说明（重要）：
    - pygame.mixer 是全局单实例播放器，无法做到“每个用户独立输出音频”。
      因此这里把“真实音频播放”当作可选能力：可播放则播放，不可播放则仅维护状态，
      方便后端接口开发与联调。
    - 用户状态按 user_id 维护，结构严格对齐 API 文档中的播放状态返回字段。
    - currentTime 通过单调时间 + 累积偏移计算，避免依赖音频设备回调。
    """

    def __init__(self, music_library_path: str | Path):
        self.music_library_path = Path(music_library_path).resolve()

        # emotion -> [Music, ...]
        self._music_by_emotion: Dict[str, List[Music]] = {e: [] for e in ALLOWED_EMOTIONS}
        # music_id -> Music
        self._music_by_id: Dict[str, Music] = {}
        # music_id -> absolute file path (server internal)
        self._file_path_by_id: Dict[str, Path] = {}

        # 用户播放状态：user_id -> state dict（用于直接返回给 API）
        self.user_states: Dict[str, Dict[str, Any]] = {}

        # 内部计时信息：user_id -> internal timing fields
        self._timing: Dict[str, Dict[str, Any]] = {}

        # pygame 初始化与并发保护（避免并发请求同时操作 mixer）
        self._mixer_ready = False
        self._lock = threading.RLock()

    # -----------------------------
    # 音乐库加载/查询
    # -----------------------------
    def load_music_library(self) -> None:
        """
        递归扫描 ./music_library 下所有 mp3 文件，按情绪分类加载并生成 m001/m002... ID。

        ID 的稳定性：
        - 为确保每次启动顺序一致，扫描结果按（emotion, 相对路径）排序后再编号。
        """
        with self._lock:
            self._music_by_emotion = {e: [] for e in ALLOWED_EMOTIONS}
            self._music_by_id = {}
            self._file_path_by_id = {}

            if not self.music_library_path.exists():
                return

            candidates: List[tuple[str, Path]] = []
            for root, _dirs, files in os.walk(self.music_library_path):
                root_path = Path(root)
                for fn in files:
                    if not fn.lower().endswith(".mp3"):
                        continue
                    full_path = (root_path / fn).resolve()
                    rel = full_path.relative_to(self.music_library_path)
                    # rel.parts[0] 期望是 anxious/happy/sad/calm
                    if not rel.parts:
                        continue
                    emotion = rel.parts[0]
                    if emotion not in ALLOWED_EMOTIONS:
                        continue
                    candidates.append((emotion, full_path))

            # 排序保证 id 稳定
            candidates.sort(key=lambda x: (x[0], x[1].as_posix()))

            for idx, (emotion, full_path) in enumerate(candidates, start=1):
                music_id = f"m{idx:03d}"
                title = full_path.stem
                artist = "疗愈音乐工作室"
                cover_url = "/images/default.jpg"

                # audioUrl 以 Web 路径形式暴露给前端（与文档示例一致：/music/<emotion>/<file>）
                rel = full_path.relative_to(self.music_library_path).as_posix()
                audio_url = f"/music/{rel}"

                duration = self._safe_get_duration_seconds(full_path)

                music = Music(
                    id=music_id,
                    title=title,
                    artist=artist,
                    coverUrl=cover_url,
                    audioUrl=audio_url,
                    duration=duration,
                )
                self._music_by_emotion[emotion].append(music)
                self._music_by_id[music_id] = music
                self._file_path_by_id[music_id] = full_path

    def get_music_by_emotion(self, emotion: str) -> List[Dict[str, Any]]:
        """根据情绪返回音乐列表（返回值为可 JSON 序列化 dict）。"""
        with self._lock:
            if emotion not in ALLOWED_EMOTIONS:
                return []
            return [m.to_dict() for m in self._music_by_emotion.get(emotion, [])]

    def get_music_by_id(self, music_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 查找音乐（返回 dict；不存在返回 None）。"""
        with self._lock:
            m = self._music_by_id.get(music_id)
            return m.to_dict() if m else None

    # -----------------------------
    # 用户状态（对齐 API 文档）
    # -----------------------------
    def get_or_create_user_state(self, user_id: str) -> Dict[str, Any]:
        """
        获取或初始化用户播放状态。

        状态字段对齐文档接口 /api/music/status 的 data：
        - playState: playing/paused/stopped/loading
        - currentTime: int 秒
        - duration: 当前曲目总时长 int 秒（无曲目时为 0）
        - volume: 0.0-1.0
        - playMode: listLoop/singleLoop/random/sequence
        - currentMusic: Music 或 null
        - playlist: [{id,title,artist,duration}, ...]
        """
        with self._lock:
            if user_id not in self.user_states:
                self.user_states[user_id] = {
                    "playState": "stopped",
                    "currentTime": 0,
                    "duration": 0,
                    "currentMusic": None,
                    "playlist": [],
                    "volume": 0.7,
                    "playMode": "listLoop",
                }
                self._timing[user_id] = {
                    "baseOffset": 0.0,  # 累积已播放秒数（暂停/seek 影响）
                    "startedAt": None,  # playing 时的 time.monotonic()
                }
            return self.user_states[user_id]

    def get_play_status(self, user_id: str) -> Dict[str, Any]:
        """返回当前播放状态（用于接口2：/api/music/status）。"""
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            st["currentTime"] = int(self._compute_current_time(user_id))
            # 防止超出 duration
            if st.get("duration", 0) and st["currentTime"] > st["duration"]:
                st["currentTime"] = int(st["duration"])
            return {
                "playState": st["playState"],
                "currentTime": st["currentTime"],
                "duration": st["duration"],
                "volume": st["volume"],
                "playMode": st["playMode"],
                "currentMusic": st["currentMusic"],
                "playlist": st["playlist"],
            }

    # -----------------------------
    # 播放控制
    # -----------------------------
    def toggle_play(self, user_id: str) -> Dict[str, Any]:
        """
        播放/暂停切换（接口3：/api/music/toggle-play）。

        规则（对齐文档描述）：
        - 当前 playing -> paused
        - 当前 paused/stopped -> playing
        - 如果没有 currentMusic，则播放“默认曲目”（按扫描顺序的第一首）
        """
        with self._lock:
            st = self.get_or_create_user_state(user_id)

            if st["playState"] == "playing":
                self._pause(user_id)
            else:
                if st["currentMusic"] is None:
                    default_id = self._first_music_id()
                    if default_id:
                        self.play_music(user_id, default_id)
                        return self.get_play_status(user_id)
                    # 没有任何音乐
                    st["playState"] = "stopped"
                    st["currentTime"] = 0
                    st["duration"] = 0
                    self._reset_timing(user_id)
                else:
                    self._resume(user_id)

            return self.get_play_status(user_id)

    def play_music(self, user_id: str, music_id: str) -> Dict[str, Any]:
        """
        播放指定曲目（接口4：/api/music/play）。

        行为：
        - currentMusic 指向该曲目
        - playlist 若为空，则用该曲目所属情绪分类下的曲目作为播放列表（简要字段）
        - currentTime 归零并开始播放
        """
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            music = self._music_by_id.get(music_id)
            if not music:
                # 由路由层根据返回值决定 404；这里抛出 KeyError 方便统一处理
                raise KeyError("music_not_found")

            st["playState"] = "loading"
            st["currentMusic"] = music.to_dict()
            st["duration"] = int(music.duration)
            st["currentTime"] = 0
            self._timing[user_id]["baseOffset"] = 0.0
            self._timing[user_id]["startedAt"] = None

            # 生成播放列表（简要信息），文档 status.playlist 只包含 id/title/artist/duration
            if not st["playlist"]:
                emotion = self._infer_emotion_by_music_id(music_id)
                if emotion:
                    st["playlist"] = [
                        {
                            "id": m.id,
                            "title": m.title,
                            "artist": m.artist,
                            "duration": int(m.duration),
                        }
                        for m in self._music_by_emotion.get(emotion, [])
                    ]
                else:
                    st["playlist"] = [
                        {"id": music.id, "title": music.title, "artist": music.artist, "duration": int(music.duration)}
                    ]

            self._play_file_now(user_id, music_id, start_pos=0.0)
            st["playState"] = "playing"
            return self.get_play_status(user_id)

    def next_track(self, user_id: str) -> Dict[str, Any]:
        """下一曲（接口6：/api/music/next）。"""
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            next_id = self._select_next_music_id(user_id, direction=1)
            if not next_id:
                raise KeyError("playlist_empty")
            return self.play_music(user_id, next_id)

    def previous_track(self, user_id: str) -> Dict[str, Any]:
        """上一曲（接口5：/api/music/previous）。"""
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            prev_id = self._select_next_music_id(user_id, direction=-1)
            if not prev_id:
                raise KeyError("playlist_empty")
            return self.play_music(user_id, prev_id)

    def seek(self, user_id: str, position: float) -> Dict[str, Any]:
        """
        进度拖拽（接口7：/api/music/seek）。

        position 单位：秒。
        """
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            dur = float(st.get("duration", 0) or 0)
            if dur <= 0:
                # 没有可 seek 的曲目，保持状态不变
                return self.get_play_status(user_id)

            # clamp 到 [0, duration]
            pos = max(0.0, min(float(position), dur))
            self._timing[user_id]["baseOffset"] = pos
            self._timing[user_id]["startedAt"] = time.monotonic() if st["playState"] == "playing" else None

            # 如果正在播放，尽量让 pygame 从该位置开始
            cm = st.get("currentMusic")
            if cm and isinstance(cm, dict) and cm.get("id"):
                self._play_file_now(user_id, cm["id"], start_pos=pos, keep_state=True)

            st["currentTime"] = int(pos)
            return self.get_play_status(user_id)

    def set_volume(self, user_id: str, volume: float) -> Dict[str, Any]:
        """
        设置音量（接口8：/api/music/volume）。

        volume 范围：0.0 - 1.0
        """
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            v = max(0.0, min(float(volume), 1.0))
            st["volume"] = float(v)
            if self._ensure_mixer():
                try:
                    pygame.mixer.music.set_volume(v)  # type: ignore[union-attr]
                except Exception:
                    pass
            return self.get_play_status(user_id)

    def set_play_mode(self, user_id: str, mode: str) -> Dict[str, Any]:
        """
        设置播放模式（接口9：/api/music/play-mode）。

        mode: listLoop/singleLoop/random/sequence
        """
        with self._lock:
            st = self.get_or_create_user_state(user_id)
            if mode in ALLOWED_PLAY_MODES:
                st["playMode"] = mode
            return self.get_play_status(user_id)

    # -----------------------------
    # 内部：mixer/播放实现
    # -----------------------------
    def _ensure_mixer(self) -> bool:
        """
        初始化 pygame mixer。

        在某些服务器/CI/无音频设备环境下 mixer.init 可能失败，
        此时返回 False 并继续以“仅维护状态”的方式运行。
        """
        if not _PYGAME_AVAILABLE:
            return False
        if self._mixer_ready:
            return True
        try:
            pygame.mixer.init()  # type: ignore[union-attr]
            self._mixer_ready = True
            return True
        except Exception:
            self._mixer_ready = False
            return False

    def _play_file_now(self, user_id: str, music_id: str, start_pos: float = 0.0, keep_state: bool = False) -> None:
        """
        立刻播放指定 music_id 对应文件（如果 mixer 可用）。

        keep_state=True 时不修改 playState，仅用于 seek 等场景。
        """
        if not self._ensure_mixer():
            # 真实播放不可用：仅更新时间信息
            self._timing[user_id]["startedAt"] = time.monotonic()
            return

        fp = self._file_path_by_id.get(music_id)
        if not fp:
            return

        try:
            pygame.mixer.music.load(str(fp))  # type: ignore[union-attr]
            # 注意：start 参数对 mp3 的支持取决于底层解码器，失败则回退从头播放
            try:
                pygame.mixer.music.play(start=float(start_pos))  # type: ignore[union-attr]
            except Exception:
                pygame.mixer.music.play()  # type: ignore[union-attr]
            # 音量按照用户状态设置（如果存在）
            st = self.get_or_create_user_state(user_id)
            try:
                pygame.mixer.music.set_volume(float(st.get("volume", 0.7)))  # type: ignore[union-attr]
            except Exception:
                pass

            if not keep_state:
                self._timing[user_id]["startedAt"] = time.monotonic()
        except Exception:
            # 播放失败：保持系统可用，不抛异常
            self._timing[user_id]["startedAt"] = time.monotonic()

    def _pause(self, user_id: str) -> None:
        st = self.get_or_create_user_state(user_id)
        # 先把 currentTime 累积到 baseOffset
        now_time = self._compute_current_time(user_id)
        self._timing[user_id]["baseOffset"] = float(now_time)
        self._timing[user_id]["startedAt"] = None
        st["playState"] = "paused"
        st["currentTime"] = int(now_time)

        if self._ensure_mixer():
            try:
                pygame.mixer.music.pause()  # type: ignore[union-attr]
            except Exception:
                pass

    def _resume(self, user_id: str) -> None:
        st = self.get_or_create_user_state(user_id)
        st["playState"] = "playing"
        self._timing[user_id]["startedAt"] = time.monotonic()
        if self._ensure_mixer():
            try:
                pygame.mixer.music.unpause()  # type: ignore[union-attr]
            except Exception:
                # 某些情况下 unpause 无效（未曾 pause），则尝试重新 play 当前曲目
                cm = st.get("currentMusic")
                if isinstance(cm, dict) and cm.get("id"):
                    self._play_file_now(user_id, cm["id"], start_pos=float(st.get("currentTime", 0) or 0), keep_state=True)

    def _reset_timing(self, user_id: str) -> None:
        if user_id not in self._timing:
            self._timing[user_id] = {"baseOffset": 0.0, "startedAt": None}
        self._timing[user_id]["baseOffset"] = 0.0
        self._timing[user_id]["startedAt"] = None

    def _compute_current_time(self, user_id: str) -> float:
        """根据 internal timing 计算 currentTime（秒）。"""
        timing = self._timing.get(user_id)
        if not timing:
            return 0.0
        base = float(timing.get("baseOffset", 0.0) or 0.0)
        started_at = timing.get("startedAt")
        if started_at is None:
            return base
        return base + (time.monotonic() - float(started_at))

    # -----------------------------
    # 内部：播放模式/切歌策略
    # -----------------------------
    def _select_next_music_id(self, user_id: str, direction: int) -> Optional[str]:
        """
        根据播放模式与方向选择下一首/上一首。

        direction:
        - 1: next
        - -1: previous
        """
        st = self.get_or_create_user_state(user_id)
        playlist = st.get("playlist") or []
        if not playlist:
            return None

        ids = [it.get("id") for it in playlist if isinstance(it, dict) and it.get("id")]
        ids = [i for i in ids if isinstance(i, str)]
        if not ids:
            return None

        current_id: Optional[str] = None
        cm = st.get("currentMusic")
        if isinstance(cm, dict):
            current_id = cm.get("id")

        mode = st.get("playMode")
        if mode not in ALLOWED_PLAY_MODES:
            mode = "listLoop"

        # singleLoop：永远返回当前曲目（若无则返回第一首）
        if mode == "singleLoop":
            return current_id if current_id in ids else ids[0]

        # random：随机挑一首（尽量避免连续重复）
        if mode == "random":
            if len(ids) == 1:
                return ids[0]
            candidates = [i for i in ids if i != current_id] or ids
            return random.choice(candidates)

        # listLoop / sequence：按索引前后移动
        if current_id in ids:
            idx = ids.index(current_id)
        else:
            idx = 0

        next_idx = idx + (1 if direction >= 0 else -1)

        if mode == "sequence":
            # 顺序播放：超出边界则表示“播完 stop”
            if next_idx < 0 or next_idx >= len(ids):
                # 进入 stopped 状态（不切歌）
                st["playState"] = "stopped"
                st["currentTime"] = 0
                st["duration"] = 0
                st["currentMusic"] = None
                self._reset_timing(user_id)
                if self._ensure_mixer():
                    try:
                        pygame.mixer.music.stop()  # type: ignore[union-attr]
                    except Exception:
                        pass
                return None
            return ids[next_idx]

        # listLoop：循环到头/尾
        if next_idx < 0:
            return ids[-1]
        if next_idx >= len(ids):
            return ids[0]
        return ids[next_idx]

    def _infer_emotion_by_music_id(self, music_id: str) -> Optional[str]:
        """
        根据 music_id 的文件路径反推情绪分类。
        该方法仅用于生成 playlist；不会影响外部 API 字段。
        """
        fp = self._file_path_by_id.get(music_id)
        if not fp:
            return None
        try:
            rel = fp.relative_to(self.music_library_path)
        except Exception:
            return None
        if not rel.parts:
            return None
        emo = rel.parts[0]
        return emo if emo in ALLOWED_EMOTIONS else None

    def _first_music_id(self) -> Optional[str]:
        """返回扫描顺序中的第一首音乐 ID（用于默认播放）。"""
        if not self._music_by_id:
            return None
        # music_id 是 m001/m002...，按字符串排序即可
        return sorted(self._music_by_id.keys())[0]

    def _safe_get_duration_seconds(self, file_path: Path) -> int:
        """
        尝试获取 mp3 时长（秒）。

        优先使用 pygame.mixer.Sound 读取长度；失败则返回 0。
        说明：
        - pygame 对 mp3 时长读取依赖底层解码器，且 mixer 初始化可能失败；
          因此这里必须容错，不能让加载音乐库失败。
        """
        if not self._ensure_mixer():
            return 0
        try:
            snd = pygame.mixer.Sound(str(file_path))  # type: ignore[union-attr]
            length = snd.get_length()
            if length is None:
                return 0
            return int(round(float(length)))
        except Exception:
            return 0

