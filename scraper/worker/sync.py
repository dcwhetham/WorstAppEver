"""The incremental sync engine.

This is the piece that must never "redownload everything". Four independent
guards stand between a sync run and a redundant byte, cheapest first:

1. **Remote id already indexed** — `media_files.remote_id` matches. Free.
2. **Remote-advertised hash already held** — the source gave us an ETag or digest
   that is already in this account's hash set. Free.
3. **File already on disk at the target path** — hash it locally and adopt it
   into the index instead of fetching it. One local read, zero network.
4. **Post-download hash collision** — the bytes turn out to be a duplicate under
   a different remote id. The temp file is discarded and nothing enters the
   archive.

Only guard 4 costs bandwidth, and only for genuinely new remote ids.

On top of that, `pacing.plan_run` caps how much a single run may fetch. A first
sync of 900 files becomes a slow drip across many runs rather than one burst that
gets the IP flagged.

Two invariants hold throughout:

* **Downloads are atomic.** Bytes go to `<name>.part` and are renamed into place
  only after hashing succeeds, so the archive never contains a truncated file
  and a killed container leaves nothing but a stray `.part`.
* **Files stay raw.** No transcode, no re-container, no metadata stripping. What
  the source served is what lands on disk.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.base import (
    AccountPrivateError,
    AccountUnavailableError,
    AdapterError,
    AdapterUnavailableError,
    BlockedError,
    ProfileInfo,
    RateLimitedError,
    RemoteItem,
    SourceAdapter,
)
from .config import RuntimeSettings, WorkerEnv
from .db import transaction
from .hashing import HashingWriter, known_hashes, sha256_file
from .logs import log_event
from .pacing import EtaTracker, jittered_delay, plan_run
from .queue import (
    defer_job,
    is_cancel_requested,
    now_iso,
    renew_lease,
    update_progress,
)

# Consecutive already-known items that end a discovery pass. Sources list
# newest-first, so a streak this long means we have reached previously seen
# history and further paging is wasted requests.
KNOWN_STREAK_LIMIT = 12

# Ceiling on a discovery pass, so a pathological source cannot spin forever.
DISCOVERY_HARD_LIMIT = 5000

# Reject absurd payloads rather than filling the disk with someone's error page.
MAX_ITEM_BYTES = 8 * 1024 * 1024 * 1024

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_EXT_FROM_URL = re.compile(r"\.([A-Za-z0-9]{2,5})(?:$|[?#])")

_DEFAULT_EXT = {"image": ".jpg", "video": ".mp4", "other": ".bin"}


@dataclass
class SyncResult:
    status: str = "succeeded"
    downloaded: int = 0
    skipped_duplicate: int = 0
    skipped_existing: int = 0
    failed: int = 0
    bytes_downloaded: int = 0
    discovered: int = 0
    remaining: int = 0
    adapter_used: str | None = None
    message: str = ""
    error_summary: str | None = None
    defer_seconds: float | None = None
    #: Set when a paced run left work behind. The *worker* queues the follow-up
    #: after this job reaches a terminal state — `idx_jobs_one_active` forbids a
    #: second live job for the account, so the engine cannot do it itself while
    #: still holding the current one.
    requeue_after_seconds: float | None = None
    notes: list[str] = field(default_factory=list)


class SyncEngine:
    """Runs one job for one account.

    Stateless between jobs: everything it needs comes from the database, so a
    restart mid-backfill resumes from `remote_index` with nothing lost.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        env: WorkerEnv,
        settings: RuntimeSettings,
        adapters: list[SourceAdapter],
    ) -> None:
        self.conn = conn
        self.env = env
        self.settings = settings
        self.adapters = sorted(adapters, key=lambda a: a.rank)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, job: sqlite3.Row) -> SyncResult:
        account_id = int(job["account_id"])
        handle = job["account_name"]
        links = self._links(account_id)
        result = SyncResult()

        if not links:
            result.status = "failed"
            result.error_summary = "no source links configured for this account"
            log_event(
                self.conn,
                level="error",
                event="no_links",
                message=f"'{handle}' has no enabled links; nothing to scrape",
                account_id=account_id,
                job_id=int(job["id"]),
            )
            return result

        self._ensure_dirs(handle, job["archive_path"])

        # ---------------- discovery ----------------
        update_progress(self.conn, int(job["id"]), phase="discover", message="Checking source for new items")
        try:
            discovered, adapter = self._discover(job, handle, links)
        except RateLimitedError as exc:
            return self._rate_limited(job, exc)
        except (AccountUnavailableError, AccountPrivateError) as exc:
            return self._unavailable(job, exc)
        except BlockedError as exc:
            return self._blocked(job, exc)

        result.discovered = discovered
        result.adapter_used = adapter.name if adapter else None
        if adapter is None:
            result.status = "failed"
            result.error_summary = "every adapter in the fallback chain failed"
            return result

        # ---------------- plan ----------------
        pending = self._pending_items(account_id)
        is_new = job["last_success_at"] is None
        plan = plan_run(
            backlog=len(pending),
            is_new=bool(is_new),
            is_favorite=bool(job["is_favorite"]),
            settings=self.settings,
            consecutive_failures=int(job["consecutive_failures"] or 0),
        )

        if plan.budget == 0:
            result.message = "Already up to date"
            update_progress(
                self.conn,
                int(job["id"]),
                phase="done",
                message=result.message,
                items_expected=0,
                eta_seconds=0,
            )
            return result

        slice_ = pending[: plan.budget]
        eta = EtaTracker(self.settings.eta_sample_window)
        eta.seed(plan.base_delay_ms)

        update_progress(
            self.conn,
            int(job["id"]),
            phase="download",
            message=plan.reason,
            items_expected=len(slice_),
            items_discovered=discovered,
            pace_delay_ms=plan.base_delay_ms,
            eta_seconds=eta.eta_seconds(len(slice_)),
        )
        log_event(
            self.conn,
            level="info",
            event="sync_planned",
            message=f"'{handle}': {plan.reason} (budget {plan.budget}, ~{plan.base_delay_ms}ms/item)",
            account_id=account_id,
            job_id=int(job["id"]),
            detail={
                "backlog": len(pending),
                "budget": plan.budget,
                "base_delay_ms": plan.base_delay_ms,
                "paced": plan.paced,
                "spread_over_runs": plan.spread_over_runs,
                "adapter": adapter.name,
            },
        )

        # ---------------- download ----------------
        hashes = known_hashes(self.conn, account_id)
        indexed_remote_ids = self._indexed_remote_ids(account_id)

        for index, row in enumerate(slice_):
            if is_cancel_requested(self.conn, int(job["id"])):
                result.status = "cancelled"
                result.message = f"Cancelled after {result.downloaded} downloads"
                return result

            item = _row_to_item(row)
            started = time.monotonic()
            try:
                outcome = self._process_item(job, account_id, handle, item, adapter, hashes, indexed_remote_ids)
            except RateLimitedError as exc:
                # No follow-up job needed: `defer_job` reschedules *this* row, and
                # the untouched `remote_index` rows are still the work list.
                result.remaining = len(pending) - index
                return self._rate_limited(job, exc, partial=result)
            except BlockedError as exc:
                result.remaining = len(pending) - index
                return self._blocked(job, exc, partial=result)
            except AdapterError as exc:
                self._fail_item(row["id"], exc)
                result.failed += 1
                log_event(
                    self.conn,
                    level="warn",
                    event="item_failed",
                    message=f"{item.remote_id}: {exc}",
                    account_id=account_id,
                    job_id=int(job["id"]),
                    error_type=type(exc).__name__,
                    retryable=exc.retryable,
                )
            except OSError as exc:
                # Disk-side problem: no space, permissions, a vanished mount.
                # Stop the run rather than failing every remaining item in turn.
                result.status = "failed"
                result.error_summary = f"filesystem error: {exc}"
                log_event(
                    self.conn,
                    level="critical",
                    event="filesystem_error",
                    message=f"Aborting run for '{handle}': {exc}",
                    account_id=account_id,
                    job_id=int(job["id"]),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
                return result
            else:
                if outcome == "downloaded":
                    result.downloaded += 1
                elif outcome == "duplicate":
                    result.skipped_duplicate += 1
                elif outcome == "adopted":
                    result.skipped_existing += 1

            elapsed = time.monotonic() - started
            eta.record(elapsed)
            remaining_in_slice = len(slice_) - index - 1
            update_progress(
                self.conn,
                int(job["id"]),
                items_downloaded=result.downloaded,
                items_skipped=result.skipped_duplicate + result.skipped_existing,
                items_failed=result.failed,
                bytes_downloaded=result.bytes_downloaded,
                eta_seconds=eta.eta_seconds(remaining_in_slice),
                message=f"{plan.reason} — {index + 1}/{len(slice_)}",
            )

            if remaining_in_slice:
                pause = jittered_delay(plan.base_delay_ms, self.settings)
                # Renew before sleeping: a long human-like pause plus a big video
                # could otherwise outlast the lease and get the job reaped.
                renew_lease(self.conn, int(job["id"]), self.env.lease_seconds)
                time.sleep(pause)

        # ---------------- leftovers ----------------
        result.remaining = max(0, len(pending) - len(slice_))
        if result.remaining:
            result.requeue_after_seconds = plan.requeue_delay_seconds
            result.notes.append(
                f"{result.remaining} item(s) deferred to a later run (~{plan.requeue_delay_seconds / 60:.0f} min)"
            )

        result.message = (
            f"{result.downloaded} downloaded, "
            f"{result.skipped_duplicate + result.skipped_existing} skipped, "
            f"{result.failed} failed" + (f", {result.remaining} queued for later" if result.remaining else "")
        )
        return result

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self, job: sqlite3.Row, handle: str, links: list[dict[str, Any]]) -> tuple[int, SourceAdapter | None]:
        """Refresh `remote_index`, walking the fallback chain until one works.

        Adapter-specific failures (unimplemented, missing cookies) move to the
        next adapter. Account-level failures (deleted, private) propagate out,
        because trying six mirrors for an account that no longer exists is both
        pointless and conspicuous.
        """
        account_id = int(job["account_id"])
        last_error: AdapterError | None = None

        for adapter in self.adapters:
            if not adapter.supports(handle, links):
                continue
            try:
                profile = adapter.probe(handle, links)
                if not profile.exists:
                    raise AccountUnavailableError(f"'{handle}' not found via {adapter.name}")
                if profile.is_private:
                    raise AccountPrivateError(f"'{handle}' is private via {adapter.name}")
                self._record_profile(account_id, profile)

                count = self._ingest_items(job, adapter, handle, links)
            except (AccountUnavailableError, AccountPrivateError, RateLimitedError, BlockedError):
                raise
            except AdapterUnavailableError as exc:
                log_event(
                    self.conn,
                    level="debug",
                    event="adapter_skipped",
                    message=f"{adapter.name} unavailable for '{handle}': {exc}",
                    account_id=account_id,
                    job_id=int(job["id"]),
                )
                last_error = exc
                continue
            except AdapterError as exc:
                log_event(
                    self.conn,
                    level="warn",
                    event="adapter_failed",
                    message=f"{adapter.name} failed for '{handle}': {exc}; trying next source",
                    account_id=account_id,
                    job_id=int(job["id"]),
                    error_type=type(exc).__name__,
                    retryable=exc.retryable,
                )
                self._mark_link_error(account_id, adapter.name)
                last_error = exc
                continue
            else:
                self._mark_link_ok(account_id, adapter.name)
                return count, adapter

        if last_error is not None:
            log_event(
                self.conn,
                level="error",
                event="all_adapters_failed",
                message=f"Every source failed for '{handle}': {last_error}",
                account_id=account_id,
                job_id=int(job["id"]),
                error_type=type(last_error).__name__,
                retryable=True,
            )
        return 0, None

    def _ingest_items(self, job: sqlite3.Row, adapter: SourceAdapter, handle: str, links: list[dict[str, Any]]) -> int:
        """Upsert listed items into `remote_index`, stopping early once known.

        The early exit is what makes routine syncs cheap: with newest-first
        ordering, a streak of already-indexed items means everything older is
        already recorded.
        """
        account_id = int(job["account_id"])
        seen_before = 0
        total = 0

        for item in adapter.list_items(handle, links, max_items=DISCOVERY_HARD_LIMIT):
            total += 1
            is_new = self._upsert_remote(account_id, item)
            seen_before = 0 if is_new else seen_before + 1

            if total % 25 == 0:
                update_progress(
                    self.conn,
                    int(job["id"]),
                    items_discovered=total,
                    message=f"Discovered {total} items via {adapter.name}",
                )
            if seen_before >= KNOWN_STREAK_LIMIT:
                break
            if total >= DISCOVERY_HARD_LIMIT:
                log_event(
                    self.conn,
                    level="warn",
                    event="discovery_truncated",
                    message=f"Stopped discovery for '{handle}' at {total} items (hard limit)",
                    account_id=account_id,
                    job_id=int(job["id"]),
                )
                break

        return total

    def _upsert_remote(self, account_id: int, item: RemoteItem) -> bool:
        """Record one remote item. Returns True when newly seen.

        `INSERT OR IGNORE` then a conditional `UPDATE`: the update deliberately
        never resets `state`, so an item already marked `downloaded` or
        `duplicate` stays that way when it reappears in a later listing.
        """
        with transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO remote_index
                    (account_id, provider, remote_id, remote_url, media_type,
                     remote_hash, remote_bytes, posted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    item.provider,
                    item.remote_id,
                    item.url,
                    item.media_type,
                    item.content_hash,
                    item.size_bytes,
                    item.posted_at,
                ),
            )
            if cursor.rowcount:
                return True
            self.conn.execute(
                """
                UPDATE remote_index
                   SET remote_url = COALESCE(?, remote_url),
                       remote_hash = COALESCE(?, remote_hash),
                       remote_bytes = COALESCE(?, remote_bytes),
                       posted_at = COALESCE(?, posted_at)
                 WHERE account_id = ? AND provider = ? AND remote_id = ?
                """,
                (
                    item.url,
                    item.content_hash,
                    item.size_bytes,
                    item.posted_at,
                    account_id,
                    item.provider,
                    item.remote_id,
                ),
            )
        return False

    # ------------------------------------------------------------------
    # Per-item download
    # ------------------------------------------------------------------

    def _process_item(
        self,
        job: sqlite3.Row,
        account_id: int,
        handle: str,
        item: RemoteItem,
        adapter: SourceAdapter,
        hashes: set[str],
        indexed_remote_ids: set[tuple[str, str]],
    ) -> str:
        """Fetch one item, or prove we already have it.

        Returns `downloaded`, `duplicate`, or `adopted`.
        """
        job_id = int(job["id"])

        # Guard 1 — this exact remote id is already indexed.
        if (item.provider, item.remote_id) in indexed_remote_ids:
            self._mark_remote(item, account_id, "duplicate", note="already indexed by remote id")
            return "duplicate"

        # Guard 2 — the source told us a hash we already hold.
        if item.content_hash and item.content_hash in hashes:
            self._mark_remote(item, account_id, "duplicate", note="remote hash already held")
            return "duplicate"

        target = self._target_path(handle, job["archive_path"], item)

        # Guard 3 — the file is already on disk. Adopt it rather than refetch.
        # This is the path that makes an existing manually-built archive import
        # for free: hash locally, index it, download nothing.
        if target.is_file() and target.stat().st_size > 0:
            local_hash = sha256_file(target)
            if local_hash in hashes:
                self._index_media(account_id, handle, job["archive_path"], target, item, local_hash, downloaded=False)
                self._mark_remote(item, account_id, "duplicate", note="present on disk")
                return "adopted"
            self._index_media(account_id, handle, job["archive_path"], target, item, local_hash, downloaded=False)
            hashes.add(local_hash)
            indexed_remote_ids.add((item.provider, item.remote_id))
            self._mark_remote(item, account_id, "downloaded", note="adopted existing file")
            return "adopted"

        if self.env.dry_run:
            self._mark_remote(item, account_id, "skipped", note="dry run")
            return "duplicate"

        # Guard 4 — download, then check the hash before it enters the archive.
        # Writing to `.part` first means a crash here leaves a temp file, never a
        # truncated file masquerading as archived media.
        partial = target.with_name(target.name + ".part")
        try:
            with adapter.open_stream(item) as stream, HashingWriter(partial) as writer:
                writer.copy_from(stream, max_bytes=MAX_ITEM_BYTES)
            content_hash = writer.hexdigest
            size = writer.bytes_written

            if size == 0:
                raise AdapterError(f"{item.remote_id}: source returned an empty body")

            if content_hash in hashes:
                partial.unlink(missing_ok=True)
                self._mark_remote(item, account_id, "duplicate", note="hash matched after download")
                return "duplicate"

            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

        self._index_media(account_id, handle, job["archive_path"], target, item, content_hash, downloaded=True)
        hashes.add(content_hash)
        indexed_remote_ids.add((item.provider, item.remote_id))
        self._mark_remote(item, account_id, "downloaded")

        update_progress(self.conn, job_id, bytes_downloaded=size)
        return "downloaded"

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _index_media(
        self,
        account_id: int,
        handle: str,
        archive_path: str | None,
        path: Path,
        item: RemoteItem,
        content_hash: str,
        *,
        downloaded: bool,
    ) -> None:
        root = self.env.account_dir(handle, archive_path)
        rel_path = path.relative_to(root).as_posix()
        stat = path.stat()
        stamp = now_iso()
        try:
            with transaction(self.conn):
                self.conn.execute(
                    """
                    INSERT INTO media_files
                        (account_id, media_type, rel_path, filename, ext, bytes, mtime_ns,
                         content_hash, source_provider, remote_id, remote_url,
                         captured_at, downloaded_at, imported_at, last_verified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        item.media_type,
                        rel_path,
                        path.name,
                        path.suffix.lower(),
                        stat.st_size,
                        stat.st_mtime_ns,
                        content_hash,
                        item.provider,
                        item.remote_id,
                        item.url,
                        item.posted_at,
                        stamp if downloaded else None,
                        None if downloaded else stamp,
                        stamp,
                    ),
                )
        except sqlite3.IntegrityError:
            # The dedup index rejected it, meaning a concurrent path indexed
            # these bytes first. The file on disk is correct either way; just
            # attach the provenance we now know.
            with transaction(self.conn):
                self.conn.execute(
                    """
                    UPDATE media_files
                       SET remote_id = COALESCE(remote_id, ?),
                           remote_url = COALESCE(remote_url, ?),
                           source_provider = COALESCE(source_provider, ?),
                           last_verified_at = ?
                     WHERE account_id = ? AND rel_path = ?
                    """,
                    (item.remote_id, item.url, item.provider, stamp, account_id, rel_path),
                )

    def _mark_remote(self, item: RemoteItem, account_id: int, state: str, note: str | None = None) -> None:
        with transaction(self.conn):
            self.conn.execute(
                """
                UPDATE remote_index
                   SET state = ?, last_attempt_at = ?, last_error = ?,
                       attempts = attempts + 1,
                       media_file_id = COALESCE(
                           (SELECT id FROM media_files
                             WHERE account_id = ? AND remote_id = ? AND deleted_at IS NULL
                             LIMIT 1),
                           media_file_id)
                 WHERE account_id = ? AND provider = ? AND remote_id = ?
                """,
                (
                    state,
                    now_iso(),
                    note,
                    account_id,
                    item.remote_id,
                    account_id,
                    item.provider,
                    item.remote_id,
                ),
            )

    def _fail_item(self, remote_row_id: int, exc: Exception) -> None:
        with transaction(self.conn):
            self.conn.execute(
                """
                UPDATE remote_index
                   SET state = CASE WHEN attempts + 1 >= 4 THEN 'gone' ELSE 'failed' END,
                       attempts = attempts + 1,
                       last_attempt_at = ?,
                       last_error = ?
                 WHERE id = ?
                """,
                (now_iso(), f"{type(exc).__name__}: {exc}"[:500], remote_row_id),
            )

    def _record_profile(self, account_id: int, profile: ProfileInfo) -> None:
        """Store remote totals so the ETA has a denominator before discovery ends."""
        with transaction(self.conn):
            self.conn.execute(
                """
                UPDATE accounts
                   SET platform_state = 'ok',
                       expected_image_count = COALESCE(?, expected_image_count),
                       expected_video_count = COALESCE(?, expected_video_count),
                       last_scrape_at = ?
                 WHERE id = ?
                """,
                (profile.image_total, profile.video_total, now_iso(), account_id),
            )

    def _mark_link_ok(self, account_id: int, provider: str) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE account_links SET last_ok_at = ? WHERE account_id = ? AND provider = ?",
                (now_iso(), account_id, provider),
            )

    def _mark_link_error(self, account_id: int, provider: str) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE account_links SET last_error_at = ? WHERE account_id = ? AND provider = ?",
                (now_iso(), account_id, provider),
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _links(self, account_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT provider, kind, url, remote_handle, sort_order
                  FROM account_links
                 WHERE account_id = ? AND is_enabled = 1
                 ORDER BY sort_order, id
                """,
                (account_id,),
            )
        ]

    def _pending_items(self, account_id: int) -> list[sqlite3.Row]:
        """Work list: discovered but not yet on disk.

        Newest-first, because if a run is budget-limited the recent items are the
        ones the user is most likely to want. `attempts < 4` drops items that have
        repeatedly failed so one dead URL cannot consume every run's budget.
        """
        return self.conn.execute(
            """
            SELECT r.*
              FROM remote_index r
             WHERE r.account_id = ?
               AND r.state IN ('pending', 'failed')
               AND r.attempts < 4
               AND NOT EXISTS (
                    SELECT 1 FROM media_files m
                     WHERE m.account_id = r.account_id
                       AND m.remote_id = r.remote_id
                       AND m.source_provider = r.provider
               )
               -- A soft-deleted file means the user removed it on purpose.
               AND NOT EXISTS (
                    SELECT 1 FROM media_files m2
                     WHERE m2.account_id = r.account_id
                       AND m2.remote_id = r.remote_id
                       AND m2.deleted_at IS NOT NULL
               )
             ORDER BY COALESCE(r.posted_at, r.discovered_at) DESC, r.id DESC
            """,
            (account_id,),
        ).fetchall()

    def _indexed_remote_ids(self, account_id: int) -> set[tuple[str, str]]:
        return {
            (row[0], row[1])
            for row in self.conn.execute(
                """
                SELECT source_provider, remote_id FROM media_files
                 WHERE account_id = ? AND remote_id IS NOT NULL AND deleted_at IS NULL
                """,
                (account_id,),
            )
            if row[0] and row[1]
        }

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _ensure_dirs(self, handle: str, archive_path: str | None) -> Path:
        root = self.env.account_dir(handle, archive_path)
        (root / self.env.photos_dir).mkdir(parents=True, exist_ok=True)
        (root / self.env.videos_dir).mkdir(parents=True, exist_ok=True)
        return root

    def _target_path(self, handle: str, archive_path: str | None, item: RemoteItem) -> Path:
        """Deterministic destination for an item.

        Determinism is what makes guard 3 work: the same remote item must always
        map to the same filename, or a re-run cannot recognise its own downloads.
        Names are date-prefixed so a plain file browser sorts chronologically —
        the archive has to be pleasant to use without this tool.
        """
        root = self.env.account_dir(handle, archive_path)
        subdir = self.env.subdir_for(item.media_type)
        ext = _extension_for(item)
        date_prefix = (item.posted_at or "")[:10].replace("-", "") or "00000000"
        stem = _UNSAFE.sub("_", item.filename_hint or item.remote_id)[:80].strip("._") or "item"
        return root / subdir / f"{date_prefix}_{stem}{ext}"

    # ------------------------------------------------------------------
    # Failure paths
    # ------------------------------------------------------------------

    def _rate_limited(self, job: sqlite3.Row, exc: RateLimitedError, partial: SyncResult | None = None) -> SyncResult:
        """Honour a 429. Deferred, not failed: nothing is wrong with the account."""
        result = partial or SyncResult()
        wait = exc.retry_after or self.settings.rate_limit_backoff_ms / 1000.0
        result.status = "deferred"
        result.defer_seconds = wait
        result.message = f"Rate limited; retrying in {wait / 60:.0f} min"
        log_event(
            self.conn,
            level="warn",
            event="rate_limited",
            message=f"'{job['account_name']}' rate limited: {exc}. Backing off {wait:.0f}s",
            account_id=int(job["account_id"]),
            job_id=int(job["id"]),
            error_type=type(exc).__name__,
            retryable=True,
            detail={"retry_after_seconds": wait},
        )
        defer_job(self.conn, int(job["id"]), wait, result.message)
        return result

    def _blocked(self, job: sqlite3.Row, exc: BlockedError, partial: SyncResult | None = None) -> SyncResult:
        """Captcha or bot wall: stop this identity for a long while.

        Retrying soon on the same proxy and cookies is how a soft block becomes a
        hard one, so the cool-off is deliberately much longer than a 429's.
        """
        result = partial or SyncResult()
        wait = max(3600.0, self.settings.rate_limit_backoff_ms / 1000.0 * 4)
        result.status = "deferred"
        result.defer_seconds = wait
        result.message = f"Blocked by source; cooling off for {wait / 3600:.1f}h"
        log_event(
            self.conn,
            level="error",
            event="blocked",
            message=f"'{job['account_name']}' blocked: {exc}. Rotate proxy/cookies before retrying",
            account_id=int(job["account_id"]),
            job_id=int(job["id"]),
            error_type=type(exc).__name__,
            retryable=True,
        )
        defer_job(self.conn, int(job["id"]), wait, result.message)
        return result

    def _unavailable(self, job: sqlite3.Row, exc: AdapterError) -> SyncResult:
        """Deleted or private: flag the account and stop scraping it.

        Flipping `scrape_enabled` off is the point. Left on, a deleted account
        generates a failed job every scheduled run forever, and the error badge
        stops meaning anything.
        """
        account_id = int(job["account_id"])
        state = "private" if isinstance(exc, AccountPrivateError) else "unavailable"
        with transaction(self.conn):
            self.conn.execute(
                """
                UPDATE accounts
                   SET platform_state = ?, status = 'flagged', scrape_enabled = 0,
                       last_error_at = ?
                 WHERE id = ?
                """,
                (state, now_iso(), account_id),
            )
        log_event(
            self.conn,
            level="error",
            event=exc.event,
            message=f"'{job['account_name']}' is {state}; flagged and scraping disabled",
            account_id=account_id,
            job_id=int(job["id"]),
            error_type=type(exc).__name__,
            retryable=False,
        )
        return SyncResult(
            status="failed",
            error_summary=str(exc),
            message=f"Account {state}; flagged for review",
        )


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------


def _row_to_item(row: sqlite3.Row) -> RemoteItem:
    return RemoteItem(
        remote_id=row["remote_id"],
        url=row["remote_url"] or "",
        media_type=row["media_type"],
        provider=row["provider"],
        content_hash=row["remote_hash"],
        size_bytes=row["remote_bytes"],
        posted_at=row["posted_at"],
    )


def _extension_for(item: RemoteItem) -> str:
    """Extension from the filename hint or URL, falling back by media type.

    Kept conservative: an unexpected extension is preserved rather than
    normalised, because renaming `.jpeg` to `.jpg` would break the determinism
    that guard 3 depends on.
    """
    for candidate in (item.filename_hint, item.url):
        if not candidate:
            continue
        match = _EXT_FROM_URL.search(candidate)
        if match:
            ext = "." + match.group(1).lower()
            if len(ext) <= 6:
                return ext
    return _DEFAULT_EXT.get(item.media_type, ".bin")


def cleanup_partials(root: Path) -> int:
    """Delete abandoned `.part` files left by a killed container.

    Safe to run unconditionally at startup: a `.part` is by definition an
    incomplete download that was never renamed into place.
    """
    removed = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*.part"):
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed
