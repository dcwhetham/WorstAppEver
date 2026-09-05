"""Structured writer for `event_log`.

Every failure the user could plausibly ask "why did this stop working?" about
lands here, which is what lets the UI show a per-account log modal instead of
sending people to `docker logs`.

Repeat suppression: identical events inside `COALESCE_WINDOW_MINUTES` bump an
`occurrences` counter rather than inserting a new row. A rate-limit storm should
read as "429 ×47" in one line, not bury the one interesting error underneath
forty-six copies of itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Literal

from .util import utc_now_iso

Level = Literal["debug", "info", "warn", "error", "critical"]
Source = Literal["backend", "scraper", "scheduler", "scanner", "web"]

COALESCE_WINDOW_MINUTES = 30

# Numbers and quoted fragments vary between otherwise identical errors (ids,
# byte counts, retry-after values), so they are masked out of the fingerprint.
_VOLATILE = re.compile(r"\d+|'[^']*'|\"[^\"]*\"")


def fingerprint(account_id: int | None, event: str, error_type: str | None, message: str) -> str:
    normalised = _VOLATILE.sub("#", message.lower())[:200]
    raw = f"{account_id or 0}|{event}|{error_type or ''}|{normalised}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def log_event(
    conn: sqlite3.Connection,
    *,
    level: Level,
    source: Source,
    event: str,
    message: str,
    account_id: int | None = None,
    job_id: int | None = None,
    detail: dict[str, Any] | None = None,
    error_type: str | None = None,
    traceback: str | None = None,
    retryable: bool = False,
) -> int:
    """Insert or coalesce a log row. Returns the row id.

    Runs on the caller's connection and does not open its own transaction, so
    logging a failure is atomic with whatever state change accompanies it.
    """
    now = utc_now_iso()
    fp = fingerprint(account_id, event, error_type, message)

    recent = conn.execute(
        """
        SELECT id, occurrences FROM event_log
         WHERE fingerprint = ?
           AND level = ?
           AND resolved_at IS NULL
           AND ts > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
         ORDER BY ts DESC LIMIT 1
        """,
        (fp, level, f"-{COALESCE_WINDOW_MINUTES} minutes"),
    ).fetchone()

    if recent is not None:
        conn.execute(
            """
            UPDATE event_log
               SET occurrences = occurrences + 1,
                   ts = ?,
                   message = ?,
                   detail = COALESCE(?, detail),
                   job_id = COALESCE(?, job_id)
             WHERE id = ?
            """,
            (now, message, json.dumps(detail) if detail else None, job_id, recent["id"]),
        )
        return int(recent["id"])

    cursor = conn.execute(
        """
        INSERT INTO event_log
            (ts, level, source, account_id, job_id, event, message, detail,
             error_type, traceback, fingerprint, first_seen_at, retryable)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            level,
            source,
            account_id,
            job_id,
            event,
            message,
            json.dumps(detail) if detail else None,
            error_type,
            traceback,
            fp,
            now,
            1 if retryable else 0,
        ),
    )
    return int(cursor.lastrowid)


def resolve_account_errors(conn: sqlite3.Connection, account_id: int, event: str | None = None) -> int:
    """Mark open errors resolved so the card's error badge clears itself.

    Called after a successful sync. Without this, a single bad night leaves a red
    badge on the card until someone dismisses it by hand, and people stop
    trusting the badge.
    """
    sql = """
        UPDATE event_log SET resolved_at = ?
         WHERE account_id = ? AND resolved_at IS NULL AND level IN ('error', 'critical')
    """
    params: list[Any] = [utc_now_iso(), account_id]
    if event:
        sql += " AND event = ?"
        params.append(event)
    return conn.execute(sql, params).rowcount


def prune_event_log(conn: sqlite3.Connection, keep_days: int = 90, keep_per_account: int = 500) -> int:
    """Trim old resolved noise. Unresolved errors are never pruned."""
    deleted = conn.execute(
        """
        DELETE FROM event_log
         WHERE resolved_at IS NOT NULL
           AND ts < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
        """,
        (f"-{keep_days} days",),
    ).rowcount
    deleted += conn.execute(
        """
        DELETE FROM event_log
         WHERE level = 'debug'
           AND id NOT IN (
                SELECT id FROM event_log
                 WHERE level = 'debug'
                 ORDER BY ts DESC
                 LIMIT ?
           )
        """,
        (keep_per_account,),
    ).rowcount
    return deleted
