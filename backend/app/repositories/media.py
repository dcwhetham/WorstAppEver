"""Media index queries, plus resolution of a row back to a real file on disk."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..util import safe_join

MEDIA_SORTS = {
    "newest": "COALESCE(captured_at, downloaded_at, first_seen_at) DESC, id DESC",
    "oldest": "COALESCE(captured_at, downloaded_at, first_seen_at) ASC, id ASC",
    "name": "filename COLLATE NOCASE ASC",
    "size": "bytes DESC",
}


def _decorate(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["is_missing"] = bool(data.get("is_missing"))
    data["raw_url"] = f"/api/media/{data['id']}/raw"
    return data


def list_for_account(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    media_type: str | None = None,
    include_missing: bool = False,
    sort: str = "newest",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ["account_id = ?", "deleted_at IS NULL"]
    params: list[Any] = [account_id]
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)
    if not include_missing:
        where.append("is_missing = 0")

    clause = " AND ".join(where)
    order = MEDIA_SORTS.get(sort, MEDIA_SORTS["newest"])

    total = conn.execute(f"SELECT COUNT(*) FROM media_files WHERE {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM media_files WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [_decorate(row) for row in rows], int(total)


def get(conn: sqlite3.Connection, media_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT m.*, a.name AS account_name, a.archive_path
          FROM media_files m
          JOIN accounts a ON a.id = m.account_id
         WHERE m.id = ?
        """,
        (media_id,),
    ).fetchone()
    return dict(row) if row else None


def resolve_path(row: dict[str, Any], settings: Settings | None = None) -> Path:
    """Absolute path for a media row.

    `rel_path` is stored data, but stored data has been wrong before, so it goes
    through `safe_join` rather than a bare concatenation.
    """
    settings = settings or get_settings()
    account_dir = settings.account_dir(row["account_name"], row.get("archive_path"))
    return safe_join(account_dir, *row["rel_path"].split("/"))


def counts_by_type(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT media_type, COUNT(*) AS n
          FROM media_files
         WHERE account_id = ? AND deleted_at IS NULL AND is_missing = 0
         GROUP BY media_type
        """,
        (account_id,),
    ).fetchall()
    return {row["media_type"]: int(row["n"]) for row in rows}


def soft_delete(conn: sqlite3.Connection, media_id: int) -> bool:
    """Tombstone a media row.

    The row survives so the scraper's incremental pass treats the file as
    "deliberately gone" instead of "missing, re-download it".
    """
    cursor = conn.execute(
        "UPDATE media_files SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND deleted_at IS NULL",
        (media_id,),
    )
    return cursor.rowcount > 0


def duplicate_report(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Identical bytes held under more than one account.

    Not automatically resolved: the same photo legitimately living in two
    accounts' folders is usually correct, and hardlinking or pruning is a
    decision for the user.
    """
    rows = conn.execute(
        """
        SELECT content_hash,
               COUNT(*)                AS copies,
               COUNT(DISTINCT account_id) AS accounts,
               MIN(bytes)              AS bytes,
               GROUP_CONCAT(DISTINCT account_id) AS account_ids
          FROM media_files
         WHERE content_hash IS NOT NULL AND deleted_at IS NULL
         GROUP BY content_hash
        HAVING COUNT(*) > 1
         ORDER BY copies DESC, bytes DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
