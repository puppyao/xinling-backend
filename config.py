from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _load_local_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            k = key.strip()
            if not k:
                continue
            v = value.strip().strip('"').strip("'")
            # Keep externally injected env vars as highest priority.
            if os.getenv(k) is None:
                os.environ[k] = v
    except Exception:
        # Do not block app startup because of malformed local env.
        return


_load_local_env_file()


@dataclass(frozen=True)
class Config:
    musicLibraryPath: Path = Path("./music_library").resolve()
    databasePath: Path = Path("./xinling.sqlite3").resolve()
    host: str = "0.0.0.0"
    port: int = 8080
    musicProviderMode: str = os.getenv("MUSIC_PROVIDER_MODE", "hybrid")
    musicApiBaseUrl: str = os.getenv("MUSIC_API_BASE_URL", "http://127.0.0.1:4000")
    playlistSyncIntervalDays: int = int(os.getenv("PLAYLIST_SYNC_INTERVAL_DAYS", "30"))
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
    OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    OPENAI_MODEL = os.getenv('OPENAI_MODEL', '')



config = Config()

