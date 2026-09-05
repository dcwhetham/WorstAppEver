"""Job claiming, leases, heartbeats and progress reporting.

Claiming is a `BEGIN IMMEDIATE` select-then-guarded-update. SQLite has no
`SKIP LOCKED`, so the write lock plus the `AND status = 'queued'` predicate is
what makes the claim atomic: if two workers race, the second one's UPDATE
matches zero rows and it moves on to the next job.

Leases exist because a container can die mid-download. The job stays `running`
with an expiry in the past, and the next worker (or the backend at boot) returns
it to the queue. Without that, the one-live-job-per-account index would block
the account forever.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import transaction

ISO = "%Y-%m-%dT%H:%M:%S.%f"


def now_iso() -> str:
    return datetime.now(UTC).strftime(ISO)[:-3] + "Z"


def offset_iso(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime(ISO)[:-3] + "Z"


def claim_next_job(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    lease_seconds: int,
    respect_schedule: bool = True,
) -> sqlite3.Row | None:
    """Claim the highest-priority due job, or return None.

    `respect_schedule=False` lets manual "Run Now" work escape the scheduled
    window; the caller decides, because a user clicking a button at 3pm should
    not be told to wait until 2am.
    """
    with transaction(conn):
        candidate = conn.execute(
            """
            SELECT id FROM scrape_jobs
             WHERE status IN ('queued', 'deferred')
               AND (? = 0 OR scheduled_for <= ?)
             ORDER BY priority DESC, scheduled_for ASC, id ASC
             LIMIT 1
            """,
            (1 if respect_schedule else 0, now_iso()),
        ).fetchone()
        if candidate is None:
            return None

        job_id = int(candidate["id"])
        updated = conn.execute(
            """
            UPDATE scrape_jobs
               SET status = 'claimed',
                   claimed_by = ?,
                   claimed_at = ?,
                   lease_expires_at = ?,
                   attempts = attempts + 1,
                   started_at = COALESCE(started_at, ?)
             WHERE id = ? AND status IN ('queued', 'deferred')
            """,
            (worker_id, now_iso(), offset_iso(lease_seconds), now_iso(), job_id),
        )
        if updated.rowcount == 0:
            # Lost the race to another worker. Returning None simply means we
            # try again on the next poll.
            return None

        return conn.execute(
            """
            SELECT j.*, a.name AS account_name, a.archive_path, a.is_favorite,
                   a.scrape_enabled, a.status AS account_status, a.platform_state,
                   a.consecutive_failures, a.last_success_at,
                   a.expected_image_count, a.expected_video_count
              FROM scrape_jobs j
              LEFT JOIN accounts a ON a.id = j.account_id
             WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()


def mark_running(conn: sqlite3.Connection, job_id: int, phase: str, message: str | None = None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE scrape_jobs SET status = 'running', phase = ?, message = COALESCE(?, message) WHERE id = ?",
            (phase, message, job_id),
        )


_PROGRESS_FIELDS = frozenset(
    {
        "items_expected",
        "items_discovered",
        "items_downloaded",
        "items_skipped",
        "items_failed",
        "bytes_downloaded",
        "eta_seconds",
        "pace_delay_ms",
        "phase",
        "message",
    }
)


def update_progress(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    """Write live progress for the dashboard's ETA timer.

    Called after every item, so it stays a single small UPDATE and never opens a
    long transaction — the dashboard has to keep reading while this runs.
    """
    updates = {key: value for key, value in fields.items() if key in _PROGRESS_FIELDS}
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with transaction(conn):
        conn.execute(
            f"UPDATE scrape_jobs SET {assignments} WHERE id = ?",
            [*updates.values(), job_id],
        )


def renew_lease(conn: sqlite3.Connection, job_id: int, lease_seconds: int) -> None:
    """Extend the lease. Called around anything slow, like a large video."""
    with transaction(conn):
        conn.execute(
            "UPDATE scrape_jobs SET lease_expires_at = ? WHERE id = ?",
            (offset_iso(lease_seconds), job_id),
        )


def is_cancel_requested(conn: sqlite3.Connection, job_id: int) -> bool:
    """Cooperative cancellation, checked at each pacing pause.

    Pausing is the natural checkpoint: nothing is in flight, so stopping there
    cannot leave a partial file behind.
    """
    row = conn.execute(
        "SELECT json_extract(COALESCE(payload, '{}'), '$.cancel_requested') AS flag FROM scrape_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    return bool(row and row["flag"])


def finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    message: str | None = None,
    error_summary: str | None = None,
) -> None:
    """Terminal transition. Triggers in 0002 roll the outcome into the account."""
    with transaction(conn):
        conn.execute(
            """
            UPDATE scrape_jobs
               SET status = ?,
                   finished_at = ?,
                   lease_expires_at = NULL,
                   phase = CASE WHEN ? = 'succeeded' THEN 'done' ELSE phase END,
                   message = COALESCE(?, message),
                   error_summary = COALESCE(?, error_summary)
             WHERE id = ?
            """,
            (status, now_iso(), status, message, error_summary, job_id),
        )


def defer_job(conn: sqlite3.Connection, job_id: int, delay_seconds: float, reason: str) -> None:
    """Push a job into the future without consuming a retry attempt.

    Used for rate limits and for outside-the-window work: neither is a failure,
    and burning attempts on them would eventually mark a healthy account failed.
    """
    with transaction(conn):
        conn.execute(
            """
            UPDATE scrape_jobs
               SET status = 'deferred',
                   scheduled_for = ?,
                   claimed_by = NULL,
                   claimed_at = NULL,
                   lease_expires_at = NULL,
                   attempts = MAX(0, attempts - 1),
                   message = ?
             WHERE id = ?
            """,
            (offset_iso(delay_seconds), reason, job_id),
        )


def enqueue_followup(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    job_type: str = "sync",
    delay_seconds: float,
    priority: int,
    payload: dict[str, Any] | None = None,
) -> int | None:
    """Queue the next slice of a paced backfill.

    Returns None when a live job already exists for the account — the unique
    index enforces that, and hitting it is the expected outcome if the user has
    meanwhile clicked "Run Now".
    """
    try:
        with transaction(conn):
            cursor = conn.execute(
                """
                INSERT INTO scrape_jobs
                    (account_id, job_type, status, trigger, priority, payload, scheduled_for)
                VALUES (?, ?, 'queued', 'schedule', ?, ?, ?)
                """,
                (
                    account_id,
                    job_type,
                    priority,
                    json.dumps(payload) if payload else None,
                    offset_iso(delay_seconds),
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        return None


def reap_expired_leases(conn: sqlite3.Connection) -> int:
    """Reclaim jobs whose worker died.

    A live job with no lease is reclaimed immediately. The schema forbids that
    state, but skipping it would leave the account wedged forever with nothing
    able to recover it, and claiming sets status and lease in one statement so
    this cannot race a worker that just claimed.
    """
    with transaction(conn):
        cursor = conn.execute(
            """
            UPDATE scrape_jobs
               SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
                   claimed_by = NULL, claimed_at = NULL, lease_expires_at = NULL,
                   error_summary = COALESCE(error_summary, 'Worker lease expired; job reclaimed'),
                   finished_at = CASE WHEN attempts >= max_attempts THEN ? ELSE NULL END
             WHERE status IN ('claimed', 'running')
               AND (lease_expires_at IS NULL OR lease_expires_at < ?)
            """,
            (now_iso(), now_iso()),
        )
        return cursor.rowcount


def heartbeat(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    status: str = "idle",
    current_job_id: int | None = None,
    version: str | None = None,
    detail: str | None = None,
) -> None:
    """Publish liveness so the dashboard can show the container's state.

    This is the whole mechanism behind "is the scraper up?" in the UI — no
    network call from the backend, just a row with a recent timestamp.
    """
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO worker_heartbeats (worker_id, kind, hostname, version, status, current_job_id, detail, beat_at)
            VALUES (?, 'scraper', ?, ?, ?, ?, ?, ?)
            ON CONFLICT (worker_id) DO UPDATE SET
                status = excluded.status,
                current_job_id = excluded.current_job_id,
                detail = excluded.detail,
                version = COALESCE(excluded.version, worker_heartbeats.version),
                beat_at = excluded.beat_at
            """,
            (worker_id, socket.gethostname(), version, status, current_job_id, detail, now_iso()),
        )
