from __future__ import annotations

from typing import Any, Dict, List

from music_player import MusicPlayer
from music_provider import MusicProvider


_EMOTION_NAME_MAP: Dict[str, str] = {
    "anxious": "焦虑",
    "happy": "开心",
    "sad": "难过",
    "calm": "平静",
    "angry": "生气",
}


class LocalMusicProvider(MusicProvider):
    def __init__(self, player: MusicPlayer):
        self.player = player

    def get_recommendations(self, *, user_id: str, emotion: str) -> List[Dict[str, Any]]:
        _ = user_id
        return self.player.get_music_by_emotion(emotion)

    def get_categories(self, *, user_id: str) -> List[Dict[str, Any]]:
        _ = user_id
        categories: List[Dict[str, Any]] = []
        for emotion in ("happy", "anxious", "sad", "calm", "angry"):
            music_list = self.player.get_music_by_emotion(emotion)
            categories.append(
                {
                    "emotion": emotion,
                    "emotionName": _EMOTION_NAME_MAP.get(emotion, emotion),
                    "count": len(music_list),
                    "coverUrl": f"/images/categories/{emotion}.jpg",
                }
            )
        return categories

    def play_music(self, *, user_id: str, music_id: str) -> Dict[str, Any]:
        if not music_id.startswith("m"):
            raise KeyError("music_not_found")
        return self.player.play_music(user_id, music_id)

    def get_lyrics(self, *, user_id: str, music_id: str) -> Dict[str, Any]:
        _ = user_id
        _ = music_id
        return {"musicId": music_id, "lines": [], "raw": "", "translatedRaw": ""}
