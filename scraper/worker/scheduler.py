"""Scheduled run blocks.

The scheduler decides *which accounts get looked at and when*; `sync.py` decides
how much work each visit does. Keeping those separate means a change to pacing
never affects rotation fairness, and vice versa.

Three rules:

* **Favourites are checked more often**, not harder. A favourite gets a shorter
  revisit interval and a queue priority boost, but the same per-run budget — the
  point is freshness, not volume.
* **Scheduled work only runs inside the configured block.** Manual "Run Now"
  jobs ignore the block entirely, because a user clicking a button at 3pm should
  not be told to wait until 2am.
* **Staleness breaks ties.** Ordering by least-recently-scraped stops the same
  favourites monopolising every block while a long tail never gets visited.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime

from .config import RuntimeSettings
from .db import transaction
from .logs import log_event
from .queue import now_iso, offset_iso

# Revisit intervals in hours. Legacy accounts are frozen archives — checking one
# daily is pure noise against a source that will never change again.
INTERVAL_HOURS = {
    "favorite": 6,
    "active": 24,
    "legacy": 168,
}

# Accounts enqueued per cycle. A block should be a steady trickle, not a stampede
# the moment the clock ticks over.
MAX_PER_CYCLE = 12

# Spacing between the jobs a single cycle queues.
STAGGER_SECONDS = (120, 420)


def revisit_interval_hours(is_favorite: bool, status: str) -> int:
    if status == "legacy":
        return INTERVAL_HOURS["legacy"]
    return INTERVAL_HOURS["favorite" if is_favorite else "active"]


def due_accounts(conn: sqlite3.Connection, limit: int = MAX_PER_CYCLE) -> list[sqlite3.Row]:
    """Accounts eligible for a scheduled sync, most deserving first.

    Excludes flagged accounts: those are deleted, banned or private, and retrying
    them every block is how the error badge stops meaning anything.
    """
    return conn.execute(
        """
        SELECT a.id, a.name, a.is_favorite, a.status, a.priority, a.last_scrape_at
          FROM accounts a
         WHERE a.scrape_enabled = 1
           AND a.status <> 'flagged'
           AND (a.next_eligible_at IS NULL OR a.next_eligible_at <= ?)
           AND NOT EXISTS (
                SELECT 1 FROM scrape_jobs j
                 WHERE j.account_id = a.id
                   AND j.status IN ('queued', 'claimed', 'running', 'deferred')
           )
         ORDER BY a.is_favorite DESC,
                  a.priority DESC,
                  -- NULL last_scrape_at means never synced; those go first.
                  CASE WHEN a.last_scrape_at IS NULL THEN 0 ELSE 1 END,
                  a.last_scrape_at ASC
         LIMIT ?
        """,
        (now_iso(), limit),
    ).fetchall()


def plan_cycle(conn: sqlite3.Connection, settings: RuntimeSettings, *, now: datetime | None = None) -> int:
    """Queue this cycle's scheduled jobs. Returns how many were created."""
    if not settings.enabled:
        return 0

    current = now or datetime.now()
    if not settings.in_scheduled_block(current.hour):
        return 0

    candidates = due_accounts(conn)
    if not candidates:
        return 0

    queued = 0
    for index, account in enumerate(candidates):
        interval = revisit_interval_hours(bool(account["is_favorite"]), account["status"])
        priority = int(account["priority"]) + (
            settings.favorite_priority_boost if account["is_favorite"] else 0
        )
        # Randomised spacing so the queue does not fill on a metronome, which is
        # visible in request timing even when individual delays are jittered.
        delay = index * random.uniform(*STAGGER_SECONDS)

        try:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO scrape_jobs
                        (account_id, job_type, status, trigger, priority, scheduled_for)
                    VALUES (?, 'sync', 'queued', 'schedule', ?, ?)
                    """,
                    (int(account["id"]), priority, offset_iso(delay)),
                )
                # Claim the slot immediately so a second scheduler pass in the
                # same block cannot double-queue the account.
                conn.execute(
                    "UPDATE accounts SET next_eligible_at = ? WHERE id = ?",
                    (offset_iso(interval * 3600), int(account["id"])),
                )
            queued += 1
        except sqlite3.IntegrityError:
            # A live job appeared between the SELECT and here. Expected under
            # concurrency; the account simply waits for the next cycle.
            continue

    if queued:
        log_event(
            conn,
            level="info",
            event="schedule_cycle",
            source="scheduler",
            message=f"Queued {queued} scheduled sync job(s)",
            detail={"hour": current.hour, "candidates": len(candidates)},
        )
    return queued


def auto_flag_failing_accounts(conn: sqlite3.Connection, settings: RuntimeSettings) -> int:
    """Flag accounts that keep failing and stop scraping them.

    An account failing every night forever is either gone or blocked, and either
    way the retries are wasted requests against a source that is already unhappy
    with us. Flagging surfaces it in the UI for a human decision instead.
    """
    threshold = max(1, settings.max_consecutive_failures)
    with transaction(conn):
        cursor = conn.execute(
            """
            UPDATE accounts
               SET status = 'flagged', scrape_enabled = 0
             WHERE scrape_enabled = 1
               AND status <> 'flagged'
               AND consecutive_failures >= ?
            """,
            (threshold,),
        )
        flagged = cursor.rowcount

    if flagged:
        log_event(
            conn,
            level="warn",
            event="accounts_auto_flagged",
            source="scheduler",
            message=f"Flagged {flagged} account(s) after {threshold} consecutive failures",
        )
    return flagged
