"""Scraper test fixtures.

Delays are set to ~1ms in the settings table so the pacing logic runs for real
(jitter, budgets, requeueing) without the tests taking minutes. That is the point
of reading pacing config from the database rather than from constants.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SCRAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRAPER_ROOT.parent
sys.path.insert(0, str(SCRAPER_ROOT))

MIGRATIONS = REPO_ROOT / "db" / "migrations"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCHIVE_DB_PATH", str(tmp_path / "archive.db"))
    monkeypatch.setenv("ARCHIVE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("MIGRATIONS_DIR", str(MIGRATIONS))
    monkeypatch.setenv("SCRAPER_FIXTURE_DIR", str(tmp_path / "fixtures"))
    monkeypatch.setenv("WORKER_ID", "test-worker")

    from worker.config import WorkerEnv

    return WorkerEnv()


@pytest.fixture
def conn(env):
    from worker.db import connect, wait_for_schema

    connection = connect(env.db_path)
    wait_for_schema(connection, env.migrations_dir)
    # Near-zero delays: exercise the pacing code paths, not the wall clock.
    connection.execute("UPDATE settings SET value = '1' WHERE key = 'scraper.min_delay_ms'")
    connection.execute("UPDATE settings SET value = '2' WHERE key = 'scraper.max_delay_ms'")
    connection.execute("UPDATE settings SET value = '0' WHERE key = 'scraper.jitter_ratio'")
    yield connection
    connection.close()


@pytest.fixture
def settings(conn):
    from worker.config import RuntimeSettings

    return RuntimeSettings.load(conn)


def make_account(
    conn: sqlite3.Connection,
    name: str,
    *,
    favorite: bool = False,
    last_success: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO accounts (name, display_name, archive_path, is_favorite, last_success_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, name, name, 1 if favorite else 0, last_success),
    )
    account_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO account_links (account_id, provider, kind, url, sort_order)
        VALUES (?, 'imginn', 'derived', ?, 20)
        """,
        (account_id, f"https://imginn.com/{name}/"),
    )
    return account_id


def queue_job(conn: sqlite3.Connection, account_id: int, *, priority: int = 0) -> sqlite3.Row:
    """Queue a job and claim it immediately.

    Clears any live job first: a paced run leaves a real follow-up job behind, and
    the one-live-job-per-account index would otherwise reject the next test step.
    """
    conn.execute(
        """
        UPDATE scrape_jobs SET status = 'cancelled'
         WHERE account_id = ? AND status IN ('queued', 'claimed', 'running', 'deferred')
        """,
        (account_id,),
    )
    conn.execute(
        """
        INSERT INTO scrape_jobs (account_id, job_type, status, trigger, priority)
        VALUES (?, 'sync', 'queued', 'manual', ?)
        """,
        (account_id, priority),
    )
    from worker.queue import claim_next_job

    job = claim_next_job(conn, "test-worker", lease_seconds=600)
    assert job is not None
    return job


def make_fixture_files(env, handle: str, *, images: int = 0, videos: int = 0) -> list[Path]:
    """Create a fixture account folder the FixtureAdapter can serve from."""
    root = Path(env.archive_root).parent / "fixtures" / handle
    (root / "photos").mkdir(parents=True, exist_ok=True)
    (root / "videos").mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for index in range(images):
        path = root / "photos" / f"img{index:03d}.jpg"
        path.write_bytes(f"image-payload-{handle}-{index}".encode() * 4)
        created.append(path)
    for index in range(videos):
        path = root / "videos" / f"vid{index:03d}.mp4"
        path.write_bytes(f"video-payload-{handle}-{index}".encode() * 8)
        created.append(path)
    return created


def run_sync(conn, env, settings, job):
    from worker.adapters.fixture import FixtureAdapter
    from worker.sync import SyncEngine

    return SyncEngine(conn, env, settings, [FixtureAdapter()]).run(job)
