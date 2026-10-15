from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default)).expanduser()
    return value if value.is_absolute() else (ROOT / value).resolve()


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8090"))
    data_dir: Path = _path("DATA_DIR", "./data")
    database_path: Path = _path("DATABASE_PATH", "./data/clipcue.sqlite3")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "").strip()
    deepgram_model: str = os.getenv("DEEPGRAM_MODEL", "nova-3").strip() or "nova-3"
    deepgram_language: str = os.getenv("DEEPGRAM_LANGUAGE", "en").strip() or "en"
    deepgram_timeout_seconds: int = int(os.getenv("DEEPGRAM_TIMEOUT_SECONDS", "570"))
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024**3)))
    max_video_seconds: int = int(os.getenv("MAX_VIDEO_SECONDS", "10800"))
    max_video_pixels: int = int(os.getenv("MAX_VIDEO_PIXELS", "2073600"))
    max_clips_per_project: int = int(os.getenv("MAX_CLIPS_PER_PROJECT", "12"))

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
