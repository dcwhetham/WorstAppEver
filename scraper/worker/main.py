"""Worker entrypoint.

The loop is intentionally boring: heartbeat, reap, schedule, claim, run, sleep.
All decisions live in `scheduler.py` and `pacing.py`; this file only sequences
them and makes sure a failure anywhere cannot take the container down.

Three properties matter for a service that is supposed to be disposable:

* **No HTTP server.** Nothing to expose, nothing to secure, no surface for the
  dashboard to depend on. Configuration arrives through the `settings` table and
  work arrives through `scrape_jobs`.
* **Settings re-read every iteration.** A toggle flipped in the browser takes
  effect on the next loop, with no restart.
* **Unhandled exceptions never exit.** They are logged, the job is failed, and
  the loop continues. A crash-restart cycle would retry the same poisoned job
  forever at container-restart speed, which is the opposite of pacing.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import time
import traceback
from types import FrameType

from .adapters import build_chain
from .config import RuntimeSettings, WorkerEnv
from .db import connect, wait_for_schema
from .logs import configure_logging, log_event
from .queue import (
    claim_next_job,
    enqueue_followup,
    finish_job,
    heartbeat,
    mark_running,
    reap_expired_leases,
    update_progress,
)
from .scheduler import auto_flag_failing_accounts, plan_cycle
from .sync import SyncEngine, cleanup_partials

logger = logging.getLogger("archive.scraper")

VERSION = "0.1.0"

# Cadence for the housekeeping that does not need to run every poll.
SCHEDULER_INTERVAL_SECONDS = 120
# Backoff after an unexpected loop failure, so a broken database does not spin.
LOOP_ERROR_BACKOFF_SECONDS = 30


class Worker:
    def __init__(self, env: WorkerEnv) -> None:
        self.env = env
        self.conn: sqlite3.Connection | None = None
        self._running = True
        self._last_schedule = 0.0

    # -- lifecycle ------------------------------------------------------

    def stop(self, signum: int, frame: FrameType | None) -> None:
        """Finish the current item, then exit.

        Cooperative rather than immediate: SIGTERM during a download would leave
        a `.part` file and a leased job. The loop checks `_running` at its
        boundaries, and any orphaned lease is reaped by whoever starts next.
        """
        logger.info("received signal %s; finishing current work and shutting down", signum)
        self._running = False

    def start(self) -> int:
        configure_logging(os.getenv("LOG_LEVEL", "INFO"))
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        logger.info("worker %s starting (version %s)", self.env.worker_id, VERSION)
        logger.info("database: %s", self.env.db_path)
        logger.info("archive:  %s", self.env.archive_root)

        self.conn = connect(self.env.db_path)
        # Either container may boot first, so the scraper applies migrations too
        # rather than waiting on the backend.
        if wait_for_schema(self.conn, self.env.migrations_dir):
            logger.info("applied pending migrations")

        self.env.archive_root.mkdir(parents=True, exist_ok=True)
        removed = cleanup_partials(self.env.archive_root)
        if removed:
            logger.info("removed %d abandoned .part file(s) from a previous run", removed)

        heartbeat(self.conn, self.env.worker_id, status="starting", version=VERSION)
        reap_expired_leases(self.conn)

        try:
            self._loop()
        finally:
            if self.conn is not None:
                heartbeat(self.conn, self.env.worker_id, status="stopping", version=VERSION)
                self.conn.close()
        logger.info("worker stopped")
        return 0

    # -- main loop ------------------------------------------------------

    def _loop(self) -> None:
        assert self.conn is not None
        while self._running:
            try:
                settings = RuntimeSettings.load(self.conn)

                if not settings.enabled:
                    # Master kill switch. Keep beating so the UI shows the worker
                    # as alive but paused, rather than as a dead container.
                    heartbeat(
                        self.conn,
                        self.env.worker_id,
                        status="paused",
                        version=VERSION,
                        detail="scraper.enabled is false",
                    )
                    time.sleep(self.env.poll_interval_seconds)
                    continue

                self._housekeeping(settings)

                job = claim_next_job(
                    self.conn,
                    self.env.worker_id,
                    lease_seconds=self.env.lease_seconds,
                    respect_schedule=True,
                )
                if job is None:
                    heartbeat(self.conn, self.env.worker_id, status="idle", version=VERSION)
                    time.sleep(self.env.poll_interval_seconds)
                    continue

                self._run_job(job, settings)

            except sqlite3.Error as exc:
                logger.error("database error in main loop: %s", exc)
                time.sleep(LOOP_ERROR_BACKOFF_SECONDS)
            except Exception:
                # Last line of defence. Exiting here would let the container's
                # restart policy retry the same poisoned job at full speed.
                logger.critical("unhandled error in main loop:\n%s", traceback.format_exc())
                time.sleep(LOOP_ERROR_BACKOFF_SECONDS)

    def _housekeeping(self, settings: RuntimeSettings) -> None:
        assert self.conn is not None
        if time.monotonic() - self._last_schedule < SCHEDULER_INTERVAL_SECONDS:
            return
        self._last_schedule = time.monotonic()
        reap_expired_leases(self.conn)
        auto_flag_failing_accounts(self.conn, settings)
        plan_cycle(self.conn, settings)

    def _run_job(self, job: sqlite3.Row, settings: RuntimeSettings) -> None:
        assert self.conn is not None
        job_id = int(job["id"])
        account_name = job["account_name"] or "(no account)"

        heartbeat(
            self.conn,
            self.env.worker_id,
            status="working",
            current_job_id=job_id,
            version=VERSION,
            detail=f"syncing {account_name}",
        )

        if job["account_id"] is None:
            finish_job(self.conn, job_id, "failed", error_summary="job has no account")
            return

        if not job["scrape_enabled"]:
            # Toggled off between enqueue and claim. Cancel rather than fail:
            # nothing went wrong, the user changed their mind.
            finish_job(self.conn, job_id, "cancelled", message="Scraping disabled for this account")
            return

        mark_running(self.conn, job_id, phase="starting", message=f"Syncing {account_name}")
        logger.info("job %s: syncing '%s' (trigger=%s)", job_id, account_name, job["trigger"])

        engine = SyncEngine(self.conn, self.env, settings, build_chain(self.env, settings))
        try:
            result = engine.run(job)
        except Exception as exc:
            # An engine bug, not a source failure. Fail this one job loudly and
            # keep the worker alive for every other account.
            logger.exception("job %s crashed", job_id)
            log_event(
                self.conn,
                level="critical",
                event="job_crashed",
                message=f"Sync crashed for '{account_name}': {exc}",
                account_id=int(job["account_id"]),
                job_id=job_id,
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
            )
            finish_job(self.conn, job_id, "failed", error_summary=f"{type(exc).__name__}: {exc}")
            return

        # `deferred` is terminal for this claim but not for the job: the row has
        # already been rescheduled, so leave it alone.
        if result.status == "deferred":
            logger.info("job %s deferred: %s", job_id, result.message)
            return

        update_progress(
            self.conn,
            job_id,
            items_downloaded=result.downloaded,
            items_skipped=result.skipped_duplicate + result.skipped_existing,
            items_failed=result.failed,
            bytes_downloaded=result.bytes_downloaded,
            eta_seconds=0,
            message=result.message,
        )
        finish_job(
            self.conn, job_id, result.status, message=result.message, error_summary=result.error_summary
        )

        # Only now can the next slice of a paced backfill be queued: the account's
        # live-job slot was occupied until the line above closed this one.
        if result.requeue_after_seconds:
            followup = enqueue_followup(
                self.conn,
                int(job["account_id"]),
                delay_seconds=result.requeue_after_seconds,
                priority=int(job["priority"]),
                payload={"continuation_of": job_id},
            )
            if followup is not None:
                logger.info(
                    "job %s: queued follow-up %s for %d remaining item(s) in ~%.0f min",
                    job_id,
                    followup,
                    result.remaining,
                    result.requeue_after_seconds / 60,
                )

        log_event(
            self.conn,
            level="info" if result.status == "succeeded" else "warn",
            event="job_finished",
            message=f"'{account_name}': {result.message}",
            account_id=int(job["account_id"]),
            job_id=job_id,
            detail={
                "status": result.status,
                "downloaded": result.downloaded,
                "skipped_duplicate": result.skipped_duplicate,
                "skipped_existing": result.skipped_existing,
                "failed": result.failed,
                "bytes": result.bytes_downloaded,
                "remaining": result.remaining,
                "adapter": result.adapter_used,
                "notes": result.notes,
            },
        )
        logger.info("job %s finished: %s", job_id, result.message)


def main() -> int:
    return Worker(WorkerEnv()).start()


if __name__ == "__main__":
    sys.exit(main())
