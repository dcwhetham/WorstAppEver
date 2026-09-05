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


# Groups of live files holding identical bytes.
#
# The `effective_hash` is the point of this query. An in-account duplicate is
# stored with `content_hash = NULL` so it cannot claim the unique dedup slot, and
# points at the row that did via `duplicate_of`. Resolving through that reference
# recovers its hash, which is what lets same-account and cross-account duplicates
# group through one code path instead of two queries that have to be merged.
#
# Without it, the copies the scanner deliberately NULLed would be invisible here —
# a scan could report "1 duplicate found" while this endpoint returned nothing.
_LIVE_HASHED = """
    SELECT m.id,
           m.account_id,
           m.rel_path,
           m.bytes,
           COALESCE(m.content_hash, o.content_hash) AS effective_hash,
           m.duplicate_of IS NOT NULL               AS is_duplicate
      FROM media_files m
      LEFT JOIN media_files o ON o.id = m.duplicate_of
     WHERE m.deleted_at IS NULL
       AND COALESCE(m.content_hash, o.content_hash) IS NOT NULL
"""


def duplicate_report(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Groups of files holding identical bytes, within or across accounts.

    Reported, never auto-resolved. The same photo legitimately living in two
    accounts' folders is usually correct, and within one account the user may have
    kept a copy on purpose — hardlinking, pruning or leaving it alone is their
    call. Each group carries its members so the UI can offer that choice against
    a specific file rather than a hash.
    """
    groups = conn.execute(
        f"""
        WITH live AS ({_LIVE_HASHED})
        SELECT effective_hash               AS content_hash,
               COUNT(*)                     AS copies,
               COUNT(DISTINCT account_id)   AS accounts,
               MIN(bytes)                   AS bytes,
               SUM(is_duplicate)            AS same_account_copies
          FROM live
         GROUP BY effective_hash
        HAVING COUNT(*) > 1
         ORDER BY copies DESC, bytes DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not groups:
        return []

    hashes = [row["content_hash"] for row in groups]
    placeholders = ",".join("?" * len(hashes))
    members: dict[str, list[dict[str, Any]]] = {h: [] for h in hashes}
    for row in conn.execute(
        f"""
        WITH live AS ({_LIVE_HASHED})
        SELECT id, account_id, rel_path, bytes, effective_hash, is_duplicate
          FROM live
         WHERE effective_hash IN ({placeholders})
         ORDER BY account_id, is_duplicate, rel_path
        """,
        hashes,
    ):
        members[row["effective_hash"]].append(
            {
                "media_id": row["id"],
                "account_id": row["account_id"],
                "rel_path": row["rel_path"],
                "bytes": row["bytes"],
                # False marks the copy holding the hash — the one to keep if the
                # user prunes the rest.
                "is_duplicate": bool(row["is_duplicate"]),
            }
        )

    out = []
    for row in groups:
        group = dict(row)
        group["same_account_copies"] = int(group["same_account_copies"] or 0)
        group["members"] = members[row["content_hash"]]
        out.append(group)
    return out
