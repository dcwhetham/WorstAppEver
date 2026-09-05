# `db/` — the only contract between services

The SQLite file is the integration point for the whole system. `/backend` and
`/scraper` never import each other's Python; they exchange work through
`scrape_jobs` and report through `event_log`. That is what makes the scraper
disposable: kill the container and the dashboard keeps serving the archive with
jobs simply sitting in `queued`.

## Layout

```
db/
├── migrations/
│   ├── 0001_init.sql      tables, indexes, default settings
│   ├── 0002_triggers.sql  counter maintenance, updated_at, job -> account rollup
│   └── 0003_views.sql     v_account_dashboard, v_scrape_queue, v_archive_stats
└── seed/
    └── demo_seed.sql      optional sample rows for UI work
```

Migrations are applied in filename order and recorded in `schema_migrations`.
Both services run the migrator on startup; it is idempotent, and whichever
container boots first wins the race harmlessly.

```bash
# apply by hand
for f in db/migrations/*.sql; do sqlite3 data/archive.db < "$f"; done

# or via the backend's migrator
python -m app.db migrate            # from ./backend
```

## Tables at a glance

| Table               | Purpose |
| ------------------- | ------- |
| `accounts`          | Primary identifier. Status, favourite/scrape toggles, counters, timestamps. |
| `account_links`     | Instagram / Imginn / Pixnoy plus user-added mirrors. Also the adapter fallback order. |
| `media_files`       | One row per raw file on disk. `content_hash` is the dedup key. |
| `remote_index`      | Everything ever seen remotely. `remote_index - media_files` is the incremental work list. |
| `scrape_jobs`       | The queue, with lease-based claiming and live progress for the ETA timer. |
| `event_log`         | Unified error/log history behind the per-account log modal. |
| `worker_heartbeats` | Liveness, so the UI can show the scraper as offline without talking to it. |
| `settings`          | Runtime knobs owned by the Web GUI. The scraper ships with no config of its own. |

## Design notes worth knowing before you change things

**Two state columns, not one.** `accounts.status` is our editorial decision
(`active` / `legacy` / `flagged`); `accounts.platform_state` is what the
platform reports (`ok` / `private` / `unavailable` / `banned`). Merging them
would make "flagged, but the account is back up" unrepresentable.

**Dedup is an index, not a code path.** `idx_media_account_hash` is a partial
unique index on `(account_id, content_hash)` for live rows. A downloader bug
that tries to write a duplicate gets an `IntegrityError` instead of quietly
doubling the archive.

**Rejected duplicates keep a back reference.** When that index fires during a
local scan — two files in one folder with identical bytes — the second row is
still indexed, because the file exists and hiding it from the UI would be a lie.
It stores `content_hash = NULL` so it cannot claim the dedup slot, and
`duplicate_of` pointing at the row that did. That column is what makes the copy
findable: a NULL hash on its own is indistinguishable from a file that has not
been hashed yet, so without it a scan could report "1 duplicate found" while
`/api/media/duplicates` returned nothing.

**Soft deletes are load-bearing.** A file the user deletes keeps its
`media_files` row with `deleted_at` set, so the scraper does not helpfully
re-download it on the next pass.

**Counters are trigger-maintained deltas.** A full-archive scan inserts
thousands of rows in one transaction; recomputing `COUNT(*)` per insert would
be quadratic, so `0002_triggers.sql` applies `+1` / `-1` adjustments instead.

**One live job per account per type.** `idx_jobs_one_active` is a partial
unique index, so mashing "Run Now" cannot stack overlapping syncs.

**A live job must carry a lease.** `CHECK (status NOT IN ('claimed', 'running')
OR lease_expires_at IS NOT NULL)`. This is what makes the reaper's guarantee
real. A claimed row with no expiry is invisible to the reaper, which has nothing
to compare, and closed to every future worker by the unique index above — so it
wedges its account permanently, which is precisely the failure the lease was
introduced to prevent. Claiming sets status and lease in one statement, so nothing
legitimate trips this.

## Concurrency

Two processes share one file, so both open it with:

```sql
PRAGMA journal_mode = WAL;      -- readers never block the writer
PRAGMA busy_timeout = 5000;     -- wait instead of throwing SQLITE_BUSY
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
```

WAL requires the DB to live on a real local filesystem. If you move `data/` to
an NFS or SMB mount, WAL locking breaks in ways that are hard to debug — bind
mount a local path instead and export the archive share separately.

If write contention ever becomes a real problem, the migration path is Postgres:
the queue uses `SELECT ... LIMIT 1` plus a guarded `UPDATE` rather than
`SKIP LOCKED`, so only `db.py` in each service needs to change.
