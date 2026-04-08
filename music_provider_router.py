from __future__ import annotations

from music_provider import MusicProvider


class MusicProviderRouter(MusicProvider):
    """
    Dispatch read APIs by provider mode.

    mode:
    - local: always use local provider
    - api: always use api provider
    - hybrid: try api first, fallback to local
    """

    def __init__(self, *, local_provider: MusicProvider, api_provider: MusicProvider, mode: str):
        self.local_provider = local_provider
        self.api_provider = api_provider
        self.mode = mode

    def get_recommendations(self, *, user_id: str, emotion: str):
        if self.mode == "local":
            return self.local_provider.get_recommendations(user_id=user_id, emotion=emotion)
        if self.mode == "api":
            return self.api_provider.get_recommendations(user_id=user_id, emotion=emotion)
        try:
            return self.api_provider.get_recommendations(user_id=user_id, emotion=emotion)
        except Exception:
            return self.local_provider.get_recommendations(user_id=user_id, emotion=emotion)

    def get_categories(self, *, user_id: str):
        if self.mode == "local":
            return self.local_provider.get_categories(user_id=user_id)
        if self.mode == "api":
            return self.api_provider.get_categories(user_id=user_id)
        try:
            return self.api_provider.get_categories(user_id=user_id)
        except Exception:
            return self.local_provider.get_categories(user_id=user_id)

    def play_music(self, *, user_id: str, music_id: str):
        is_local_id = music_id.startswith("m")
        is_api_id = music_id.startswith("nm_")
        if self.mode == "local":
            return self.local_provider.play_music(user_id=user_id, music_id=music_id)
        if self.mode == "api":
            return self.api_provider.play_music(user_id=user_id, music_id=music_id)
        if is_local_id:
            return self.local_provider.play_music(user_id=user_id, music_id=music_id)
        if is_api_id:
            return self.api_provider.play_music(user_id=user_id, music_id=music_id)
        raise KeyError("music_not_found")

    def get_lyrics(self, *, user_id: str, music_id: str):
        is_local_id = music_id.startswith("m")
        is_api_id = music_id.startswith("nm_")
        if self.mode == "local":
            return self.local_provider.get_lyrics(user_id=user_id, music_id=music_id)
        if self.mode == "api":
            return self.api_provider.get_lyrics(user_id=user_id, music_id=music_id)
        if is_local_id:
            return self.local_provider.get_lyrics(user_id=user_id, music_id=music_id)
        if is_api_id:
            try:
                return self.api_provider.get_lyrics(user_id=user_id, music_id=music_id)
            except Exception:
                return self.local_provider.get_lyrics(user_id=user_id, music_id=music_id)
        return self.local_provider.get_lyrics(user_id=user_id, music_id=music_id)
