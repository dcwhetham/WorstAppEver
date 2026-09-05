"""Runtime configuration, resolved from the environment.

Everything is env-driven so the same image runs under docker-compose and bare
`uvicorn` during development. Paths are resolved to absolute at import time so a
later `os.chdir` (uvicorn reload workers do this) cannot move the archive.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


def _path(env: str, default: Path) -> Path:
    raw = os.getenv(env)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _csv(env: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(env, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: _path("ARCHIVE_DB_PATH", REPO_ROOT / "data" / "archive.db"))
    archive_root: Path = field(default_factory=lambda: _path("ARCHIVE_ROOT", REPO_ROOT / "archive"))
    migrations_dir: Path = field(default_factory=lambda: _path("MIGRATIONS_DIR", REPO_ROOT / "db" / "migrations"))
    bundle_cache_dir: Path = field(default_factory=lambda: _path("BUNDLE_CACHE_DIR", REPO_ROOT / "data" / "bundles"))

    cors_origins: list[str] = field(default_factory=lambda: _csv("CORS_ORIGINS", "http://localhost:3000"))
    api_prefix: str = "/api"

    # Every env-derived field uses default_factory so values are read when a
    # Settings is constructed rather than when this module is imported. A plain
    # `os.getenv(...)` default is evaluated once at import and then silently
    # ignores any later change.
    #
    # Subfolder names inside each account directory. Changing these does not
    # rewrite existing folders; the scanner just stops recognising the old ones.
    photos_dir: str = field(default_factory=lambda: os.getenv("PHOTOS_DIR", "photos"))
    videos_dir: str = field(default_factory=lambda: os.getenv("VIDEOS_DIR", "videos"))

    # Files below this size are almost always placeholders or truncated
    # downloads, not media worth indexing.
    min_media_bytes: int = field(default_factory=lambda: int(os.getenv("MIN_MEDIA_BYTES", "1024")))

    # Streaming reads keep memory flat when hashing multi-GB videos.
    hash_chunk_bytes: int = field(default_factory=lambda: int(os.getenv("HASH_CHUNK_BYTES", str(1024 * 1024))))

    @property
    def image_extensions(self) -> frozenset[str]:
        return frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif", ".bmp", ".tiff"})

    @property
    def video_extensions(self) -> frozenset[str]:
        return frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".flv"})

    def account_dir(self, account_name: str, archive_path: str | None = None) -> Path:
        """Absolute path to an account folder.

        `archive_path` overrides the derived name, which matters for accounts
        renamed on the platform whose folder should not move.
        """
        return self.archive_root / (archive_path or account_name)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
