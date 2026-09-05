"""Job queue writes.

The backend only ever *enqueues*. Claiming, running and completing jobs is the
scraper's business, which is what keeps the two services independent: the
backend has no idea whether a worker exists, and does not fail when none does.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..logs import log_event
from ..util import iso_offset, utc_now_iso

# Staleness window for `worker_heartbeats`. Workers beat every ~30s, so three
# missed beats reads as offline.
HEARTBEAT_TIMEOUT_SECONDS = 90


def _favorite_boost(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = 'scraper.favorite_priority_boost'").fetchone()
    try:
        return int(row["value"]) if row else 50
    except (TypeError, ValueError):
        return 50


def effective_priority(conn: sqlite3.Connection, account_id: int, explicit: int | None = None) -> int:
    """Manual priority plus the favourite boost.

    Computed at enqueue time rather than at claim time so that un-favouriting an
    account does not reshuffle work already sitting in the queue.
    """
    if explicit is not None:
        return explicit
    row = conn.execute("SELECT priority, is_favorite FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        return 0
    return int(row["priority"]) + (_favorite_boost(conn) if row["is_favorite"] else 0)


def estimate_expected_items(conn: sqlite3.Connection, account_id: int) -> int:
    """Seed `items_expected` so the ETA bar has a denominator immediately.

    Prefer the concrete `remote_index` backlog; fall back to the expected-count
    gap for accounts that have only been probed. The scraper overwrites this
    with the real number once discovery finishes.
    """
    row = conn.execute(
        "SELECT pending_remote_count, estimated_missing_count FROM v_account_dashboard WHERE id = ?",
        (account_id,),
    ).fetchone()
    if row is None:
        return 0
    return int(row["pending_remote_count"] or 0) or int(row["estimated_missing_count"] or 0)


def enqueue(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    job_type: str = "sync",
    trigger: str = "manual",
    priority: int | None = None,
    payload: dict[str, Any] | None = None,
    delay_seconds: float = 0.0,
    requested_by: str | None = None,
) -> tuple[int, bool]:
    """Queue a job. Returns `(job_id, created)`.

    `created=False` means an identical live job already exists — the partial
    unique index `idx_jobs_one_active` collapses a double-clicked "Run Now" into
    the job that is already pending instead of raising at the user.
    """
    scheduled_for = iso_offset(delay_seconds) if delay_seconds else utc_now_iso()
    try:
        cursor = conn.execute(
            """
            INSERT INTO scrape_jobs
                (account_id, job_type, status, trigger, priority, requested_by,
                 payload, scheduled_for, items_expected)
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                job_type,
                trigger,
                effective_priority(conn, account_id, priority),
                requested_by,
                json.dumps(payload) if payload else None,
                scheduled_for,
                estimate_expected_items(conn, account_id),
            ),
        )
    except sqlite3.IntegrityError:
        existing = conn.execute(
            """
            SELECT id FROM scrape_jobs
             WHERE account_id = ? AND job_type = ?
               AND status IN ('queued', 'claimed', 'running')
             LIMIT 1
            """,
            (account_id, job_type),
        ).fetchone()
        if existing is None:
            raise
        return int(existing["id"]), False

    job_id = int(cursor.lastrowid)
    log_event(
        conn,
        level="info",
        source="backend",
        event="job_queued",
        message=f"Queued {job_type} job ({trigger})",
        account_id=account_id,
        job_id=job_id,
    )
    return job_id, True


def enqueue_batch(
    conn: sqlite3.Connection,
    account_ids: list[int],
    *,
    job_type: str = "sync",
    priority: int | None = None,
    stagger_seconds: float = 45.0,
) -> dict[str, Any]:
    """Queue many accounts at once, staggered.

    A bulk scrape of forty accounts firing simultaneously is exactly the traffic
    shape that gets an IP throttled, so each job's `scheduled_for` is pushed out
    a little further than the last.
    """
    queued: list[int] = []
    skipped: list[int] = []
    for index, account_id in enumerate(account_ids):
        job_id, created = enqueue(
            conn,
            account_id,
            job_type=job_type,
            trigger="batch",
            priority=priority,
            delay_seconds=index * stagger_seconds,
        )
        (queued if created else skipped).append(job_id)
    return {"queued": queued, "already_pending": skipped, "count": len(queued)}


def list_jobs(
    conn: sqlite3.Connection,
    *,
    account_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        where.append("account_id = ?")
        params.append(account_id)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM v_scrape_queue {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [dict(row) for row in rows]


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM v_scrape_queue WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def cancel(conn: sqlite3.Connection, job_id: int) -> bool:
    """Cancel a job that has not been claimed yet.

    A running job is left alone deliberately: the worker owns its lease, and
    yanking the row would leave a half-written file with no owner. The worker
    checks for cancellation at its next pacing pause.
    """
    cursor = conn.execute(
        """
        UPDATE scrape_jobs
           SET status = 'cancelled', finished_at = ?
         WHERE id = ? AND status IN ('queued', 'deferred')
        """,
        (utc_now_iso(), job_id),
    )
    return cursor.rowcount > 0


def request_cancel_running(conn: sqlite3.Connection, job_id: int) -> bool:
    """Flag a running job for cooperative cancellation at its next checkpoint."""
    cursor = conn.execute(
        """
        UPDATE scrape_jobs
           SET payload = json_set(COALESCE(payload, '{}'), '$.cancel_requested', 1)
         WHERE id = ? AND status IN ('claimed', 'running')
        """,
        (job_id,),
    )
    return cursor.rowcount > 0


def retry(conn: sqlite3.Connection, job_id: int) -> int | None:
    """Re-queue a clone of a failed job, preserving its payload."""
    row = conn.execute(
        "SELECT account_id, job_type, payload, priority FROM scrape_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None or row["account_id"] is None:
        return None
    new_id, _ = enqueue(
        conn,
        int(row["account_id"]),
        job_type=row["job_type"],
        trigger="retry",
        priority=int(row["priority"]),
        payload=json.loads(row["payload"]) if row["payload"] else None,
    )
    return new_id


def reap_expired_leases(conn: sqlite3.Connection) -> int:
    """Return jobs from dead workers to the queue.

    A killed container leaves a job in `running` with an expired lease. Without
    reaping, `idx_jobs_one_active` would block that account forever.
    """
    cursor = conn.execute(
        """
        UPDATE scrape_jobs
           SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
               claimed_by = NULL,
               claimed_at = NULL,
               lease_expires_at = NULL,
               error_summary = COALESCE(error_summary, 'Worker lease expired; job reclaimed'),
               finished_at = CASE WHEN attempts >= max_attempts THEN ? ELSE NULL END
         WHERE status IN ('claimed', 'running')
           AND lease_expires_at IS NOT NULL
           AND lease_expires_at < ?
        """,
        (utc_now_iso(), utc_now_iso()),
    )
    return cursor.rowcount


def workers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT *,
               CASE WHEN beat_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-{HEARTBEAT_TIMEOUT_SECONDS} seconds')
                    THEN 1 ELSE 0 END AS is_alive
          FROM worker_heartbeats
         ORDER BY beat_at DESC
        """
    ).fetchall()
    return [{**dict(row), "is_alive": bool(row["is_alive"])} for row in rows]
