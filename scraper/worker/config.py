"""Scraper configuration.

Split in two on purpose:

* **Environment** (`WorkerEnv`) — where things are: database path, archive root,
  proxy list. Deployment concerns, fixed for the container's lifetime.
* **Database** (`RuntimeSettings`) — how to behave: delays, budgets, thresholds.
  Read fresh from the `settings` table on every loop.

That second half is what "zero user intervention, all configuration through the
Web GUI" means mechanically. The container ships with no config file, and a
toggle flipped in the browser takes effect on the next iteration without a
restart.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _path(env: str, default: Path) -> Path:
    raw = os.getenv(env)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _csv(env: str) -> list[str]:
    return [item.strip() for item in os.getenv(env, "").split(",") if item.strip()]


@dataclass(frozen=True)
class WorkerEnv:
    db_path: Path = field(default_factory=lambda: _path("ARCHIVE_DB_PATH", REPO_ROOT / "data" / "archive.db"))
    archive_root: Path = field(default_factory=lambda: _path("ARCHIVE_ROOT", REPO_ROOT / "archive"))
    migrations_dir: Path = field(default_factory=lambda: _path("MIGRATIONS_DIR", REPO_ROOT / "db" / "migrations"))

    # Every field uses default_factory so the environment is read when a
    # WorkerEnv is constructed, not when this module is imported. A plain
    # `os.getenv(...)` default would be evaluated once at import and then ignore
    # any later change, which is impossible to spot and painful to debug.
    photos_dir: str = field(default_factory=lambda: os.getenv("PHOTOS_DIR", "photos"))
    videos_dir: str = field(default_factory=lambda: os.getenv("VIDEOS_DIR", "videos"))

    # Identity in `worker_heartbeats` and `scrape_jobs.claimed_by`. Defaults to
    # hostname plus a short random suffix so two containers on one host never
    # collide on a lease.
    worker_id: str = field(
        default_factory=lambda: os.getenv("WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    )

    poll_interval_seconds: float = field(default_factory=lambda: float(os.getenv("POLL_INTERVAL_SECONDS", "10")))
    heartbeat_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))
    )
    # Lease length. Must exceed the longest plausible single-file download plus
    # its pacing delay, or a healthy worker gets its own job reaped mid-run.
    lease_seconds: int = field(default_factory=lambda: int(os.getenv("LEASE_SECONDS", "1800")))

    # Rotation hooks. Empty means "direct connection", which is the honest
    # default rather than pretending to be stealthy.
    proxies: list[str] = field(default_factory=lambda: _csv("SCRAPER_PROXIES"))
    cookie_dir: Path = field(default_factory=lambda: _path("COOKIE_DIR", REPO_ROOT / "data" / "cookies"))

    request_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("REQUEST_TIMEOUT_SECONDS", "45")))
    dry_run: bool = field(default_factory=lambda: os.getenv("SCRAPER_DRY_RUN", "").lower() in {"1", "true", "yes"})

    def account_dir(self, name: str, archive_path: str | None = None) -> Path:
        return self.archive_root / (archive_path or name)

    def subdir_for(self, media_type: str) -> str:
        if media_type == "image":
            return self.photos_dir
        if media_type == "video":
            return self.videos_dir
        return "other"


@dataclass(frozen=True)
class RuntimeSettings:
    """Snapshot of the `settings` table, taken once per loop iteration."""

    enabled: bool = True
    max_concurrent_jobs: int = 1
    block_start_hour: int = 2
    block_end_hour: int = 6
    min_delay_ms: int = 4000
    max_delay_ms: int = 15000
    jitter_ratio: float = 0.45
    items_per_run: int = 25
    backlog_pace_threshold: int = 5
    new_account_ramp_runs: int = 6
    favorite_priority_boost: int = 50
    max_consecutive_failures: int = 5
    rate_limit_backoff_ms: int = 900_000
    user_agent_rotation: bool = True
    proxy_rotation: bool = False
    eta_sample_window: int = 20

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> RuntimeSettings:
        """Read the settings table, ignoring anything malformed.

        A bad value falls back to the dataclass default rather than crashing the
        worker: a typo in the settings UI should degrade behaviour, not take the
        scraper offline until someone fixes the row by hand.
        """
        rows = {
            row["key"]: (row["value"], row["value_type"])
            for row in conn.execute("SELECT key, value, value_type FROM settings")
        }
        kwargs: dict[str, object] = {}
        for name in cls.__dataclass_fields__:
            for prefix in ("scraper.", "archive."):
                entry = rows.get(prefix + name)
                if entry is None:
                    continue
                raw, value_type = entry
                try:
                    if value_type == "int":
                        kwargs[name] = int(raw)
                    elif value_type == "float":
                        kwargs[name] = float(raw)
                    elif value_type == "bool":
                        kwargs[name] = str(raw).lower() in {"1", "true", "yes", "on"}
                    else:
                        kwargs[name] = raw
                except (TypeError, ValueError):
                    pass
                break
        return cls(**kwargs)  # type: ignore[arg-type]

    def in_scheduled_block(self, hour: int) -> bool:
        """Whether `hour` falls inside the scheduled run window.

        Handles windows that wrap midnight (22 -> 4), which is the common case
        for "run overnight".
        """
        if self.block_start_hour == self.block_end_hour:
            return True  # degenerate window means "always"
        if self.block_start_hour < self.block_end_hour:
            return self.block_start_hour <= hour < self.block_end_hour
        return hour >= self.block_start_hour or hour < self.block_end_hour
