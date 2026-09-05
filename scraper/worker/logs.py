"""Event logging for the scraper.

Writes to both stderr (so `docker logs` still works when the DB is unreachable)
and `event_log` (so the dashboard can show failures without anyone opening a
terminal). The database write is the one that matters for the product; the
stderr line is the fallback for when the database is the thing that is broken.

Repeat coalescing matches the backend's: identical events inside the window bump
`occurrences` instead of inserting new rows, so a rate-limit storm reads as one
line with a count.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from typing import Any

logger = logging.getLogger("archive.scraper")

COALESCE_WINDOW_MINUTES = 30
_VOLATILE = re.compile(r"\d+|'[^']*'|\"[^\"]*\"")

_STDERR_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def fingerprint(account_id: int | None, event: str, error_type: str | None, message: str) -> str:
    normalised = _VOLATILE.sub("#", message.lower())[:200]
    return hashlib.sha256(f"{account_id or 0}|{event}|{error_type or ''}|{normalised}".encode()).hexdigest()[
        :32
    ]


def log_event(
    conn: sqlite3.Connection | None,
    *,
    level: str,
    event: str,
    message: str,
    account_id: int | None = None,
    job_id: int | None = None,
    detail: dict[str, Any] | None = None,
    error_type: str | None = None,
    traceback: str | None = None,
    retryable: bool = False,
    source: str = "scraper",
) -> None:
    logger.log(_STDERR_LEVELS.get(level, logging.INFO), "[%s] %s", event, message)
    if conn is None:
        return

    fp = fingerprint(account_id, event, error_type, message)
    try:
        recent = conn.execute(
            """
            SELECT id FROM event_log
             WHERE fingerprint = ? AND level = ? AND resolved_at IS NULL
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
                       ts = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                       message = ?,
                       detail = COALESCE(?, detail),
                       job_id = COALESCE(?, job_id)
                 WHERE id = ?
                """,
                (message, json.dumps(detail) if detail else None, job_id, recent["id"]),
            )
            return

        conn.execute(
            """
            INSERT INTO event_log
                (level, source, account_id, job_id, event, message, detail,
                 error_type, traceback, fingerprint, first_seen_at, retryable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
            """,
            (
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
                1 if retryable else 0,
            ),
        )
    except sqlite3.Error as exc:
        # Never let logging take down a run. The stderr line above already
        # happened, so the information is not lost.
        logger.warning("could not persist event to event_log: %s", exc)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
