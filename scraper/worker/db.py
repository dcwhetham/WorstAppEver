"""SQLite access for the scraper.

Intentionally a near-copy of `backend/app/db.py`. The two services share a
*schema*, not a codebase — a shared library would be an import path along which
a backend refactor could break the scraper, which is the exact coupling this
architecture exists to avoid. It is roughly eighty lines; the independence is
worth more than the duplication costs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    # Longer than the backend's: the scraper is the writer, and it would rather
    # wait out a dashboard read than fail a job that has already downloaded
    # megabytes.
    "PRAGMA busy_timeout = 15000",
    "PRAGMA synchronous = NORMAL",
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query_one(conn: sqlite3.Connection, sql: str, params: Any = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def query_all(conn: sqlite3.Connection, sql: str, params: Any = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def wait_for_schema(conn: sqlite3.Connection, migrations_dir: Path) -> bool:
    """Ensure the schema exists, applying migrations if the backend has not yet.

    Both containers can boot in either order. Whichever gets there first applies
    the migrations; the other finds the versions already recorded and skips them.
    """
    have_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    applied: set[str] = set()
    if have_table:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    changed = False
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.stem in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (path.stem,))
        changed = True
    return changed
