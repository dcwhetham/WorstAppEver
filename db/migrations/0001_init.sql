-- ---------------------------------------------------------------------------
-- 0001_init.sql — core tables for the media archive dashboard
--
-- Target: SQLite 3.35+ (WAL mode, STRICT-ish typing via CHECK constraints).
--
-- The schema is the ONLY contract shared between /backend and /scraper.
-- Neither service imports code from the other; they communicate exclusively
-- through the `scrape_jobs` queue and the `event_log` table. That keeps the
-- scraper disposable: if it crashes, the dashboard keeps serving the archive.
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Migration bookkeeping
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT    PRIMARY KEY,
    applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    checksum    TEXT
);

-- ---------------------------------------------------------------------------
-- accounts — the primary identifier for everything in the archive.
--
-- `name` doubles as the on-disk folder name: /archive/<name>/{photos,videos}
-- so it is unique and case-insensitive to match real filesystem behaviour.
--
-- Two orthogonal state columns, deliberately kept separate:
--   status         — what WE decided about the account (active / legacy / flagged)
--   platform_state — what the PLATFORM says (ok / private / unavailable / banned)
-- A banned account is `platform_state='banned'` and usually `status='flagged'`;
-- collapsing them into one column loses the ability to say "flagged but back up".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT    NOT NULL COLLATE NOCASE,
    display_name          TEXT,
    platform              TEXT    NOT NULL DEFAULT 'instagram',

    status                TEXT    NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active', 'legacy', 'flagged')),
    platform_state        TEXT    NOT NULL DEFAULT 'unknown'
                                  CHECK (platform_state IN ('unknown', 'ok', 'private', 'unavailable', 'banned')),

    is_favorite           INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
    scrape_enabled        INTEGER NOT NULL DEFAULT 1 CHECK (scrape_enabled IN (0, 1)),
    -- Higher wins when the scheduler builds a run block. Favourites get a
    -- boost at schedule time; this is the manual override on top of that.
    priority              INTEGER NOT NULL DEFAULT 0,

    -- Relative to ARCHIVE_ROOT. NULL means "derive from name".
    archive_path          TEXT,

    -- Denormalised counters, maintained by triggers in 0002. Reading these
    -- keeps the dashboard grid to a single query instead of N aggregates.
    image_count           INTEGER NOT NULL DEFAULT 0,
    video_count           INTEGER NOT NULL DEFAULT 0,
    other_count           INTEGER NOT NULL DEFAULT 0,
    total_bytes           INTEGER NOT NULL DEFAULT 0,

    -- Best known remote totals, written by the scraper's discovery pass.
    -- The gap against image_count/video_count drives the ETA progress timer.
    expected_image_count  INTEGER,
    expected_video_count  INTEGER,

    last_download_at      TEXT,   -- last time a byte actually landed on disk
    last_import_at        TEXT,   -- last local filesystem scan that adopted files
    last_scrape_at        TEXT,   -- last time the scraper touched the account
    last_success_at       TEXT,
    last_error_at         TEXT,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,

    -- Set by the scheduler so it can spread accounts across run blocks.
    next_eligible_at      TEXT,

    notes                 TEXT,
    created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_name        ON accounts (name);
CREATE INDEX        IF NOT EXISTS idx_accounts_status      ON accounts (status);
CREATE INDEX        IF NOT EXISTS idx_accounts_favorite    ON accounts (is_favorite, status);
CREATE INDEX        IF NOT EXISTS idx_accounts_scrapeable  ON accounts (scrape_enabled, next_eligible_at)
                                  WHERE scrape_enabled = 1;

-- ---------------------------------------------------------------------------
-- account_links — Instagram / Imginn / Pixnoy plus any manual mirror the user
-- pastes in. `provider` drives the icon in the UI; `kind` separates the
-- auto-derived links from user-curated ones so a re-derive never clobbers
-- manual entries.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    provider      TEXT    NOT NULL DEFAULT 'custom'
                          CHECK (provider IN ('instagram', 'imginn', 'pixnoy', 'picuki',
                                              'dumpor', 'sotwe', 'twitter', 'custom')),
    kind          TEXT    NOT NULL DEFAULT 'manual'
                          CHECK (kind IN ('primary', 'derived', 'manual')),
    url           TEXT    NOT NULL,
    label         TEXT,
    -- Manual mirrors can point at a different handle than accounts.name.
    remote_handle TEXT,
    is_enabled    INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    -- Fallback order for the scraper's adapter chain. Lower is tried first.
    sort_order    INTEGER NOT NULL DEFAULT 100,
    last_ok_at    TEXT,
    last_error_at TEXT,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_links_account_url ON account_links (account_id, url);
CREATE INDEX        IF NOT EXISTS idx_links_account     ON account_links (account_id, sort_order);

-- ---------------------------------------------------------------------------
-- media_files — one row per raw file on disk. Files stay untouched: no
-- transcoding, no renaming into opaque hashes, no sidecar containers. The DB
-- is an index over the archive, never the source of truth for bytes.
--
-- `content_hash` is a SHA-256 of the file body and is the deduplication key.
-- The partial unique index below is what makes "never download the same thing
-- twice" a database invariant rather than a hopeful code path.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id       INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,

    media_type       TEXT    NOT NULL CHECK (media_type IN ('image', 'video', 'other')),
    -- Path relative to the account folder, e.g. 'photos/2024-05-01_ABC123.jpg'.
    rel_path         TEXT    NOT NULL,
    filename         TEXT    NOT NULL,
    ext              TEXT,
    bytes            INTEGER NOT NULL DEFAULT 0,

    -- Filesystem mtime in nanoseconds. Together with `bytes` this lets a rescan
    -- skip re-hashing untouched files, which is the difference between a scan
    -- taking seconds and re-reading the entire archive every time.
    mtime_ns         INTEGER,

    content_hash     TEXT,           -- sha256 hex, NULL until hashed
    -- Optional cheap pre-filter (dhash/phash) for near-duplicate detection.
    perceptual_hash  TEXT,

    -- Provenance. NULL for files the user dropped in manually.
    source_provider  TEXT,
    remote_id        TEXT,
    remote_url       TEXT,

    width            INTEGER,
    height           INTEGER,
    duration_seconds REAL,

    captured_at      TEXT,           -- original post time when known
    downloaded_at    TEXT,           -- when the scraper fetched it
    imported_at      TEXT,           -- when a local scan first indexed it
    first_seen_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_verified_at TEXT,

    -- Set when a scan can no longer find the file. Rows are kept so the
    -- scraper does not re-download something the user deliberately deleted
    -- until they explicitly purge it.
    is_missing       INTEGER NOT NULL DEFAULT 0 CHECK (is_missing IN (0, 1)),
    deleted_at       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_media_account_path
    ON media_files (account_id, rel_path);

-- Dedup invariant: one live copy of any given content per account.
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_account_hash
    ON media_files (account_id, content_hash)
    WHERE content_hash IS NOT NULL AND deleted_at IS NULL;

-- Cross-account lookups: "do we already have these bytes anywhere?"
CREATE INDEX IF NOT EXISTS idx_media_hash        ON media_files (content_hash)
    WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_remote      ON media_files (account_id, source_provider, remote_id);
CREATE INDEX IF NOT EXISTS idx_media_account_typ ON media_files (account_id, media_type, deleted_at);
CREATE INDEX IF NOT EXISTS idx_media_recent      ON media_files (account_id, captured_at DESC);

-- ---------------------------------------------------------------------------
-- remote_index — everything the scraper has ever *seen* remotely, whether or
-- not it was downloaded. This is the other half of incremental sync: the diff
-- is `remote_index` minus `media_files`, so a discovery pass is cheap and a
-- re-run never re-enumerates from scratch.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS remote_index (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    provider       TEXT    NOT NULL,
    remote_id      TEXT    NOT NULL,
    remote_url     TEXT,
    media_type     TEXT    NOT NULL DEFAULT 'other'
                           CHECK (media_type IN ('image', 'video', 'other')),

    -- Hash/size advertised by the source (ETag, Content-Length, CDN digest).
    -- Lets us skip a download before spending bandwidth on it.
    remote_hash    TEXT,
    remote_bytes   INTEGER,
    posted_at      TEXT,

    state          TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (state IN ('pending', 'downloaded', 'duplicate',
                                            'failed', 'gone', 'skipped')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error     TEXT,

    media_file_id  INTEGER REFERENCES media_files (id) ON DELETE SET NULL,
    discovered_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_unique
    ON remote_index (account_id, provider, remote_id);
CREATE INDEX IF NOT EXISTS idx_remote_pending
    ON remote_index (account_id, state, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_hash
    ON remote_index (remote_hash) WHERE remote_hash IS NOT NULL;

-- ---------------------------------------------------------------------------
-- scrape_jobs — the queue. Backend writes rows, scraper claims them with a
-- leased UPDATE. No HTTP call from backend to scraper, so the scraper being
-- down looks like "jobs sit queued", not "the dashboard 500s".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    -- NULL for maintenance jobs that are not account-scoped.
    account_id        INTEGER REFERENCES accounts (id) ON DELETE CASCADE,
    job_type          TEXT    NOT NULL DEFAULT 'sync'
                              CHECK (job_type IN ('sync', 'discover', 'backfill',
                                                  'verify', 'probe')),
    status            TEXT    NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'claimed', 'running', 'succeeded',
                                                'failed', 'cancelled', 'deferred')),
    trigger           TEXT    NOT NULL DEFAULT 'manual'
                              CHECK (trigger IN ('manual', 'schedule', 'batch', 'retry', 'bootstrap')),
    priority          INTEGER NOT NULL DEFAULT 0,
    requested_by      TEXT,
    payload           TEXT,   -- JSON blob, adapter-specific options

    -- Lease-based claiming: a worker that dies leaves an expired lease which
    -- the next worker reaps, instead of a job stuck in 'running' forever.
    claimed_by        TEXT,
    claimed_at        TEXT,
    lease_expires_at  TEXT,

    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    scheduled_for     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at        TEXT,
    finished_at       TEXT,

    -- Live progress, polled by the dashboard for the ETA timer.
    items_expected    INTEGER NOT NULL DEFAULT 0,
    items_discovered  INTEGER NOT NULL DEFAULT 0,
    items_downloaded  INTEGER NOT NULL DEFAULT 0,
    items_skipped     INTEGER NOT NULL DEFAULT 0,
    items_failed      INTEGER NOT NULL DEFAULT 0,
    bytes_downloaded  INTEGER NOT NULL DEFAULT 0,
    eta_seconds       INTEGER,
    -- Current self-pacing delay, surfaced in the UI as "pacing: 42s/item".
    pace_delay_ms     INTEGER,
    phase             TEXT,
    message           TEXT,
    error_summary     TEXT,

    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_claimable
    ON scrape_jobs (status, priority DESC, scheduled_for)
    WHERE status IN ('queued', 'deferred');
CREATE INDEX IF NOT EXISTS idx_jobs_account  ON scrape_jobs (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_lease    ON scrape_jobs (status, lease_expires_at);

-- At most one live job per account per type. Prevents a trigger-happy "Run
-- Now" click from queueing ten overlapping syncs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_one_active
    ON scrape_jobs (account_id, job_type)
    WHERE status IN ('queued', 'claimed', 'running') AND account_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- event_log — unified log/error history. This is what the per-account "Log
-- viewer" modal reads, so nobody has to `docker logs` the scraper to find out
-- why an account stopped syncing.
--
-- `fingerprint` collapses repeats (same error, same account) so a rate-limit
-- storm shows as "×47" instead of 47 rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    level         TEXT    NOT NULL DEFAULT 'info'
                          CHECK (level IN ('debug', 'info', 'warn', 'error', 'critical')),
    source        TEXT    NOT NULL DEFAULT 'backend'
                          CHECK (source IN ('backend', 'scraper', 'scheduler', 'scanner', 'web')),
    account_id    INTEGER REFERENCES accounts (id) ON DELETE CASCADE,
    job_id        INTEGER REFERENCES scrape_jobs (id) ON DELETE SET NULL,

    event         TEXT    NOT NULL,   -- machine-readable, e.g. 'rate_limited'
    message       TEXT    NOT NULL,   -- human-readable one-liner
    detail        TEXT,               -- JSON: request ids, adapter, proxy label
    error_type    TEXT,               -- exception class name
    traceback     TEXT,

    -- Hash of (account, event, error_type, normalised message).
    fingerprint   TEXT,
    occurrences   INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT,
    retryable     INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
    -- Set when a later success supersedes the failure, so the card's error
    -- badge clears itself without manual dismissal.
    resolved_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_log_account ON event_log (account_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_log_job     ON event_log (job_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_log_level   ON event_log (level, ts DESC);
CREATE INDEX IF NOT EXISTS idx_log_unresolved
    ON event_log (account_id, ts DESC)
    WHERE level IN ('error', 'critical') AND resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_log_fingerprint ON event_log (fingerprint, account_id);

-- ---------------------------------------------------------------------------
-- worker_heartbeats — how the dashboard knows the scraper container is alive
-- without talking to it. A stale row renders as an offline badge.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id     TEXT    PRIMARY KEY,
    kind          TEXT    NOT NULL DEFAULT 'scraper',
    hostname      TEXT,
    version       TEXT,
    status        TEXT    NOT NULL DEFAULT 'idle'
                          CHECK (status IN ('starting', 'idle', 'working', 'paused',
                                            'backoff', 'stopping', 'error')),
    current_job_id INTEGER REFERENCES scrape_jobs (id) ON DELETE SET NULL,
    detail        TEXT,
    started_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    beat_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- settings — runtime knobs the Web GUI owns. The scraper reads these on every
-- loop, which is how "all configuration lives in the GUI" is enforced: the
-- container ships with no config file of its own.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    value_type  TEXT NOT NULL DEFAULT 'string'
                     CHECK (value_type IN ('string', 'int', 'float', 'bool', 'json')),
    description TEXT,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO settings (key, value, value_type, description) VALUES
    ('scraper.enabled',              'true', 'bool',  'Master kill switch for all scraping'),
    ('scraper.max_concurrent_jobs',  '1',    'int',   'Parallel account syncs per worker'),
    ('scraper.block_start_hour',     '2',    'int',   'Local hour scheduled run blocks may begin'),
    ('scraper.block_end_hour',       '6',    'int',   'Local hour scheduled run blocks must stop'),
    ('scraper.min_delay_ms',         '4000', 'int',   'Floor for inter-request delay'),
    ('scraper.max_delay_ms',         '15000','int',   'Ceiling for inter-request delay'),
    ('scraper.jitter_ratio',         '0.45', 'float', 'Randomised fraction applied to each delay'),
    ('scraper.items_per_run',        '25',   'int',   'Hard cap on downloads per account per run'),
    ('scraper.backlog_pace_threshold','5',   'int',   'Missing-file count that switches on self-pacing'),
    ('scraper.new_account_ramp_runs','6',    'int',   'Runs a brand new account is spread across'),
    ('scraper.favorite_priority_boost','50', 'int',   'Priority added to favourited accounts'),
    ('scraper.max_consecutive_failures','5', 'int',   'Failures before an account is auto-flagged'),
    ('scraper.rate_limit_backoff_ms','900000','int',  'Cool-off after a detected rate limit'),
    ('scraper.user_agent_rotation',  'true', 'bool',  'Rotate UA strings between requests'),
    ('scraper.proxy_rotation',       'false','bool',  'Enable the proxy rotation hook'),
    ('archive.verify_hash_on_scan',  'true', 'bool',  'Recompute hashes for changed files during scans'),
    ('archive.eta_sample_window',    '20',   'int',   'Downloads averaged when estimating ETA');

INSERT OR IGNORE INTO schema_migrations (version) VALUES ('0001_init');
