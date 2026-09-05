"""Test fixtures.

Each test gets its own database and archive root in a tmp dir. `get_settings` is
`lru_cache`d, so the cache has to be cleared after the environment is patched or
the first test's paths leak into every later one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setenv("ARCHIVE_DB_PATH", str(tmp_path / "archive.db"))
    monkeypatch.setenv("ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("MIGRATIONS_DIR", str(REPO_ROOT / "db" / "migrations"))
    monkeypatch.setenv("BUNDLE_CACHE_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("MIN_MEDIA_BYTES", "1")

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    from app.db import migrate

    migrate(settings)
    yield settings
    get_settings.cache_clear()


@pytest.fixture
def conn(env):
    from app.db import connect

    connection = connect(env)
    yield connection
    connection.close()


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def write_media(root: Path, account: str, subdir: str, name: str, payload: bytes) -> Path:
    path = root / account / subdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path
