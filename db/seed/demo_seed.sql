-- ---------------------------------------------------------------------------
-- demo_seed.sql — optional sample rows so the dashboard has something to
-- render before a real scan. Safe to re-run; safe to never run.
--
--   sqlite3 data/archive.db < db/seed/demo_seed.sql
-- ---------------------------------------------------------------------------

INSERT OR IGNORE INTO accounts
    (name, display_name, status, platform_state, is_favorite, scrape_enabled, priority,
     expected_image_count, expected_video_count, last_download_at, last_import_at,
     last_scrape_at, last_success_at, notes)
VALUES
    ('aurora.films', 'Aurora Films', 'active', 'ok', 1, 1, 10,
     412, 38, '2026-08-30T23:14:02Z', '2026-08-30T23:20:11Z',
     '2026-08-30T23:14:02Z', '2026-08-30T23:14:02Z', 'Primary reference account.'),
    ('neon.district', 'Neon District', 'active', 'ok', 0, 1, 0,
     188, 12, '2026-08-28T02:40:55Z', '2026-08-28T02:44:10Z',
     '2026-08-28T02:40:55Z', '2026-08-28T02:40:55Z', NULL),
    ('kestrel.archive', 'Kestrel (archive)', 'legacy', 'unavailable', 1, 0, 0,
     NULL, NULL, '2025-11-02T18:02:00Z', '2026-01-14T09:00:00Z',
     '2025-11-02T18:02:00Z', '2025-11-02T18:02:00Z', 'Account deleted upstream; archive is frozen.'),
    ('halcyon.studio', 'Halcyon Studio', 'flagged', 'banned', 0, 0, 0,
     NULL, NULL, '2026-02-19T04:31:00Z', '2026-02-19T04:35:00Z',
     '2026-07-01T03:00:00Z', '2026-02-19T04:31:00Z', 'Suspended on platform. Mirrors only.'),
    ('vantablack.co', 'Vantablack', 'active', 'unknown', 0, 1, 0,
     84, 12, NULL, NULL, NULL, NULL, 'Newly added; first sync is being paced.');

-- Derived links follow the same handle; manual mirrors can differ.
INSERT OR IGNORE INTO account_links (account_id, provider, kind, url, label, sort_order)
SELECT a.id, 'instagram', 'primary', 'https://www.instagram.com/' || a.name || '/', 'Instagram', 10 FROM accounts a
UNION ALL
SELECT a.id, 'imginn', 'derived', 'https://imginn.com/' || a.name || '/', 'Imginn', 20 FROM accounts a
UNION ALL
SELECT a.id, 'pixnoy', 'derived', 'https://www.pixnoy.com/profile/' || a.name || '/', 'Pixnoy', 30 FROM accounts a;

INSERT OR IGNORE INTO account_links (account_id, provider, kind, url, label, remote_handle, sort_order)
SELECT id, 'custom', 'manual', 'https://imginn.com/aurora.films.backup/', 'Backup handle', 'aurora.films.backup', 40
FROM accounts WHERE name = 'aurora.films';

-- A first-sync account with a live job, so the ETA timer has something to show.
--
-- The lease is required, not decorative: the schema rejects a claimed or running
-- job without one, because such a row is invisible to the reaper and blocked by
-- `idx_jobs_one_active`, which would wedge this account permanently. Setting it in
-- the past means a real worker reclaims this demo job on its first pass instead of
-- the seed quietly blocking the account it is meant to showcase.
INSERT INTO scrape_jobs
    (account_id, job_type, status, trigger, priority, phase, message,
     items_expected, items_discovered, items_downloaded, items_skipped,
     bytes_downloaded, eta_seconds, pace_delay_ms, started_at, claimed_by,
     claimed_at, lease_expires_at)
SELECT id, 'sync', 'running', 'schedule', 50, 'download',
       'Paced first sync: 14 of 96 items',
       96, 96, 14, 3, 48219004, 1180, 9500,
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-6 minutes'), 'scraper-demo',
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-6 minutes'),
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 minute')
FROM accounts WHERE name = 'vantablack.co';

INSERT INTO scrape_jobs (account_id, job_type, status, trigger, priority, scheduled_for)
SELECT id, 'sync', 'queued', 'schedule', 50, strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+2 minutes')
FROM accounts WHERE name = 'aurora.films';

INSERT INTO event_log (account_id, level, source, event, message, detail, error_type, retryable, ts)
SELECT id, 'error', 'scraper', 'account_unavailable',
       'Profile returned 404 on all adapters (instagram, imginn, pixnoy)',
       '{"adapters_tried":["instagram","imginn","pixnoy"],"status":404}',
       'AccountUnavailableError', 0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-3 hours')
FROM accounts WHERE name = 'halcyon.studio';

INSERT INTO event_log (account_id, level, source, event, message, detail, error_type, retryable, occurrences, ts)
SELECT id, 'warn', 'scraper', 'rate_limited',
       'Source responded 429; backing off for 15 minutes',
       '{"retry_after":900,"adapter":"imginn"}', 'RateLimitedError', 1, 4,
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-40 minutes')
FROM accounts WHERE name = 'neon.district';

INSERT INTO event_log (account_id, level, source, event, message, ts)
SELECT id, 'info', 'scanner', 'scan_complete', 'Indexed 450 files, 0 new, 0 missing',
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 day')
FROM accounts WHERE name = 'aurora.films';
