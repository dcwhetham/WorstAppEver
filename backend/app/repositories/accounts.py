"""Account queries backing the dashboard grid and the expanded account view."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..links import ensure_derived_links
from ..logs import log_event
from ..models import AccountCreate, AccountUpdate
from ..scanner import ensure_account_dirs
from ..util import utc_now_iso

# Whitelist, because these interpolate into the ORDER BY clause. Never accept a
# raw sort string from the client here.
SORT_COLUMNS: dict[str, str] = {
    "name": "name COLLATE NOCASE ASC",
    "recent": "COALESCE(last_download_at, last_import_at, created_at) DESC",
    "added": "created_at DESC",
    "media": "media_count DESC",
    "size": "total_bytes DESC",
    "errors": "unresolved_error_count DESC, last_error_ts DESC",
    "backlog": "estimated_missing_count DESC, pending_remote_count DESC",
}

# Favourites float to the top of every sort; it is the point of favouriting.
_FAVORITE_FIRST = "is_favorite DESC"


def _bool(value: Any) -> bool:
    return bool(value)


def row_to_card(row: sqlite3.Row) -> dict[str, Any]:
    """Flatten a `v_account_dashboard` row into the nested API shape."""
    data = dict(row)

    active_job = None
    if data.get("active_job_id"):
        expected = data.get("active_items_expected") or 0
        done = (data.get("active_items_downloaded") or 0) + (data.get("active_items_skipped") or 0)
        active_job = {
            "id": data["active_job_id"],
            "status": data["active_job_status"],
            "job_type": data["active_job_type"],
            "phase": data.get("active_job_phase"),
            "items_expected": expected,
            "items_downloaded": data.get("active_items_downloaded") or 0,
            "items_skipped": data.get("active_items_skipped") or 0,
            "items_failed": data.get("active_items_failed") or 0,
            "bytes_downloaded": data.get("active_bytes_downloaded") or 0,
            "eta_seconds": data.get("active_eta_seconds"),
            "pace_delay_ms": data.get("active_pace_delay_ms"),
            "started_at": data.get("active_started_at"),
            "message": data.get("active_message"),
            "percent_complete": min(100, round(100 * done / expected)) if expected else None,
        }

    last_error = None
    if data.get("last_error_id"):
        last_error = {
            "id": data["last_error_id"],
            "ts": data["last_error_ts"],
            "level": data["last_error_level"],
            "event": data["last_error_event"],
            "message": data["last_error_message"],
            "retryable": _bool(data.get("last_error_retryable")),
        }

    cover_id = data.get("cover_media_id")
    card = {key: value for key, value in data.items() if not key.startswith(("active_", "last_error_", "cover_"))}
    card.update(
        is_favorite=_bool(data["is_favorite"]),
        scrape_enabled=_bool(data["scrape_enabled"]),
        is_new=_bool(data.get("is_new")),
        active_job=active_job,
        last_error=last_error,
        cover_url=f"/api/media/{cover_id}/raw" if cover_id else None,
    )
    return card


def list_cards(
    conn: sqlite3.Connection,
    *,
    search: str | None = None,
    favorite: bool | None = None,
    status: str | None = None,
    scrape_enabled: bool | None = None,
    has_errors: bool | None = None,
    sort: str = "name",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    params: list[Any] = []

    if search:
        where.append("(name LIKE ? COLLATE NOCASE OR COALESCE(display_name, '') LIKE ? COLLATE NOCASE)")
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern]
    if favorite is not None:
        where.append("is_favorite = ?")
        params.append(1 if favorite else 0)
    if status:
        where.append("status = ?")
        params.append(status)
    if scrape_enabled is not None:
        where.append("scrape_enabled = ?")
        params.append(1 if scrape_enabled else 0)
    if has_errors is not None:
        where.append("unresolved_error_count > 0" if has_errors else "unresolved_error_count = 0")

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = f"{_FAVORITE_FIRST}, {SORT_COLUMNS.get(sort, SORT_COLUMNS['name'])}"

    total = conn.execute(f"SELECT COUNT(*) FROM v_account_dashboard {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM v_account_dashboard {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [row_to_card(row) for row in rows], int(total)


def get_card(conn: sqlite3.Connection, account_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM v_account_dashboard WHERE id = ?", (account_id,)).fetchone()
    return row_to_card(row) if row else None


def get_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM v_account_dashboard WHERE name = ?", (name,)).fetchone()
    return row_to_card(row) if row else None


def list_links(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM account_links WHERE account_id = ? ORDER BY sort_order, id",
        (account_id,),
    ).fetchall()
    return [{**dict(r), "is_enabled": _bool(r["is_enabled"])} for r in rows]


def create(conn: sqlite3.Connection, payload: AccountCreate) -> int:
    cursor = conn.execute(
        """
        INSERT INTO accounts
            (name, display_name, platform, status, is_favorite, scrape_enabled, priority, notes, archive_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.display_name or payload.name,
            payload.platform,
            payload.status,
            1 if payload.is_favorite else 0,
            1 if payload.scrape_enabled else 0,
            payload.priority,
            payload.notes,
            payload.name,
        ),
    )
    account_id = int(cursor.lastrowid)

    ensure_derived_links(conn, account_id, payload.name)
    for extra in payload.links:
        from ..links import add_manual_link

        add_manual_link(conn, account_id, extra)

    # Folders are created up front so the user can drop files in immediately,
    # before the scraper has ever run.
    ensure_account_dirs(payload.name)

    log_event(
        conn,
        level="info",
        source="backend",
        event="account_created",
        message=f"Account '{payload.name}' added",
        account_id=account_id,
    )
    return account_id


_UPDATABLE = {
    "display_name": str,
    "status": str,
    "platform_state": str,
    "is_favorite": int,
    "scrape_enabled": int,
    "priority": int,
    "notes": str,
    "expected_image_count": int,
    "expected_video_count": int,
}


def update(conn: sqlite3.Connection, account_id: int, patch: AccountUpdate) -> bool:
    changes = patch.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return False

    assignments: list[str] = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in _UPDATABLE:
            continue
        assignments.append(f"{key} = ?")
        params.append(int(value) if _UPDATABLE[key] is int and isinstance(value, bool) else value)

    if not assignments:
        return False

    # Re-enabling scraping should let the scheduler pick the account up on the
    # next pass rather than waiting out a backoff set while it was disabled.
    if changes.get("scrape_enabled"):
        assignments.append("next_eligible_at = NULL")
        assignments.append("consecutive_failures = 0")

    params.append(account_id)
    cursor = conn.execute(f"UPDATE accounts SET {', '.join(assignments)} WHERE id = ?", params)
    return cursor.rowcount > 0


def bulk_update(conn: sqlite3.Connection, account_ids: list[int], patch: AccountUpdate) -> int:
    return sum(1 for account_id in account_ids if update(conn, account_id, patch))


def delete(conn: sqlite3.Connection, account_id: int) -> bool:
    """Remove the account and its index rows. Files on disk are left alone.

    Deleting an account is a metadata operation. Nothing here should ever be
    able to erase the archive — that stays a manual `rm` the user performs
    knowingly.
    """
    row = conn.execute("SELECT name FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    log_event(
        conn,
        level="warn",
        source="backend",
        event="account_deleted",
        message=f"Account '{row['name']}' removed from index (files on disk untouched)",
    )
    return True


def mark_platform_state(conn: sqlite3.Connection, account_id: int, state: str, *, flag: bool = False) -> None:
    conn.execute(
        """
        UPDATE accounts
           SET platform_state = ?,
               status = CASE WHEN ? = 1 THEN 'flagged' ELSE status END,
               last_error_at = ?
         WHERE id = ?
        """,
        (state, 1 if flag else 0, utc_now_iso(), account_id),
    )
