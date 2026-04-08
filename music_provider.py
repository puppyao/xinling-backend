from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class MusicProvider(ABC):
    """Unified provider interface for music read APIs."""

    @abstractmethod
    def get_recommendations(self, *, user_id: str, emotion: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_categories(self, *, user_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def play_music(self, *, user_id: str, music_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_lyrics(self, *, user_id: str, music_id: str) -> Dict[str, Any]:
        raise NotImplementedError
