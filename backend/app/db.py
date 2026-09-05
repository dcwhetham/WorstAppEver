"""SQLite access layer and migration runner.

One connection per request. SQLite connections are cheap to open and are not
safe to share across the threadpool that FastAPI runs sync endpoints in, so
pooling would buy contention rather than speed.

The scraper has its own near-identical copy of this module. That duplication is
deliberate: the two containers share a schema, not a codebase, so neither can
break the other by refactoring.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

# WAL lets the dashboard read while the scraper writes. busy_timeout turns the
# remaining brief writer collisions into a wait instead of an exception.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA temp_store = MEMORY",
)


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    settings = settings or get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        settings.db_path,
        timeout=15.0,
        isolation_level=None,  # explicit transactions; see transaction()
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


@contextmanager
def get_connection(settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(settings)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """IMMEDIATE so the write lock is taken up front.

    With deferred transactions SQLite upgrades to a write lock on the first
    write, which can fail with SQLITE_BUSY after work has already been done.
    Taking the lock at BEGIN turns that into a clean wait.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query_all(conn: sqlite3.Connection, sql: str, params: Any = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def query_one(conn: sqlite3.Connection, sql: str, params: Any = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def query_scalar(conn: sqlite3.Connection, sql: str, params: Any = (), default: Any = None) -> Any:
    row = conn.execute(sql, params).fetchone()
    return default if row is None else row[0]


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------

_STATEMENT_SPLIT_SAFE = re.compile(r";\s*$", re.MULTILINE)


def applied_versions(conn: sqlite3.Connection) -> set[str]:
    exists = query_scalar(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'",
    )
    if not exists:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(settings: Settings | None = None, *, verbose: bool = False) -> list[str]:
    """Apply pending migrations in filename order. Idempotent.

    Both services call this at startup. If they race, the loser's transaction
    simply finds the version already recorded and skips it.
    """
    settings = settings or get_settings()
    files = sorted(settings.migrations_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no migrations found in {settings.migrations_dir}")

    applied: list[str] = []
    with get_connection(settings) as conn:
        done = applied_versions(conn)
        for path in files:
            version = path.stem
            if version in done:
                continue
            # executescript() commits any open transaction and runs the file as
            # one unit, which is what we want for DDL: SQLite DDL is
            # transactional, so a failing migration leaves nothing behind.
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            applied.append(version)
            if verbose:
                print(f"applied {version}", file=sys.stderr)
    return applied


def seed(path: Path, settings: Settings | None = None) -> None:
    with get_connection(settings) as conn:
        conn.executescript(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    command = sys.argv[1] if len(sys.argv) > 1 else "migrate"
    cfg = get_settings()
    if command == "migrate":
        changed = migrate(cfg, verbose=True)
        print(f"database: {cfg.db_path}")
        print("up to date" if not changed else f"applied: {', '.join(changed)}")
    elif command == "seed":
        target = Path(sys.argv[2]) if len(sys.argv) > 2 else cfg.migrations_dir.parent / "seed" / "demo_seed.sql"
        migrate(cfg)
        seed(target, cfg)
        print(f"seeded from {target}")
    else:
        print("usage: python -m app.db [migrate|seed [file.sql]]", file=sys.stderr)
        raise SystemExit(2)
