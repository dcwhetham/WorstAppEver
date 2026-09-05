-- ---------------------------------------------------------------------------
-- 0003_views.sql — read models for the dashboard.
--
-- The grid renders dozens of cards, each needing counters, live job progress,
-- backlog size and error state. These views keep that to one query so the UI
-- never fans out into per-card requests.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS v_account_dashboard;
CREATE VIEW v_account_dashboard AS
SELECT
    a.id,
    a.name,
    a.display_name,
    a.platform,
    a.status,
    a.platform_state,
    a.is_favorite,
    a.scrape_enabled,
    a.priority,
    a.archive_path,
    a.image_count,
    a.video_count,
    a.other_count,
    a.total_bytes,
    a.image_count + a.video_count + a.other_count            AS media_count,
    a.expected_image_count,
    a.expected_video_count,
    a.last_download_at,
    a.last_import_at,
    a.last_scrape_at,
    a.last_success_at,
    a.last_error_at,
    a.consecutive_failures,
    a.next_eligible_at,
    a.notes,
    a.created_at,
    a.updated_at,

    -- Backlog: what discovery knows about but disk does not have yet. The
    -- remote_index count is authoritative; the expected_* columns are the
    -- fallback for accounts that have only had a cheap profile probe.
    (SELECT COUNT(*) FROM remote_index r
      WHERE r.account_id = a.id AND r.state = 'pending')      AS pending_remote_count,
    MAX(0, COALESCE(a.expected_image_count, a.image_count) - a.image_count)
        + MAX(0, COALESCE(a.expected_video_count, a.video_count) - a.video_count)
                                                             AS estimated_missing_count,

    -- Live job, if any. Drives the ETA progress timer on the card footer.
    j.id                                                     AS active_job_id,
    j.status                                                 AS active_job_status,
    j.job_type                                               AS active_job_type,
    j.phase                                                  AS active_job_phase,
    j.items_expected                                         AS active_items_expected,
    j.items_downloaded                                       AS active_items_downloaded,
    j.items_skipped                                          AS active_items_skipped,
    j.items_failed                                           AS active_items_failed,
    j.bytes_downloaded                                       AS active_bytes_downloaded,
    j.eta_seconds                                            AS active_eta_seconds,
    j.pace_delay_ms                                           AS active_pace_delay_ms,
    j.started_at                                             AS active_started_at,
    j.message                                                AS active_message,

    -- Most recent unresolved failure, for the error badge and log modal.
    e.id                                                     AS last_error_id,
    e.ts                                                     AS last_error_ts,
    e.level                                                  AS last_error_level,
    e.event                                                  AS last_error_event,
    e.message                                                AS last_error_message,
    e.retryable                                              AS last_error_retryable,
    (SELECT COUNT(*) FROM event_log el
      WHERE el.account_id = a.id
        AND el.resolved_at IS NULL
        AND el.level IN ('error', 'critical'))               AS unresolved_error_count,

    -- Newly added accounts get the ramped/paced treatment in the scraper and
    -- a "first sync" affordance in the UI.
    CASE WHEN a.last_success_at IS NULL THEN 1 ELSE 0 END    AS is_new,
    (SELECT COUNT(*) FROM account_links l
      WHERE l.account_id = a.id AND l.is_enabled = 1)        AS link_count,

    -- Card artwork: newest present image. Served as raw bytes like everything
    -- else, so there is no thumbnail cache to invalidate or regenerate.
    (SELECT m.id FROM media_files m
      WHERE m.account_id = a.id
        AND m.media_type = 'image'
        AND m.deleted_at IS NULL
        AND m.is_missing = 0
      ORDER BY COALESCE(m.captured_at, m.first_seen_at) DESC, m.id DESC
      LIMIT 1)                                               AS cover_media_id
FROM accounts a
LEFT JOIN scrape_jobs j
       ON j.id = (SELECT id FROM scrape_jobs
                   WHERE account_id = a.id
                     AND status IN ('queued', 'claimed', 'running')
                   ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'claimed' THEN 1 ELSE 2 END,
                            priority DESC, id
                   LIMIT 1)
LEFT JOIN event_log e
       ON e.id = (SELECT id FROM event_log
                   WHERE account_id = a.id
                     AND resolved_at IS NULL
                     AND level IN ('error', 'critical')
                   ORDER BY ts DESC, id DESC
                   LIMIT 1);

-- ---------------------------------------------------------------------------
-- v_scrape_queue — operator view of the queue, newest first.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_scrape_queue;
CREATE VIEW v_scrape_queue AS
SELECT
    j.*,
    a.name AS account_name,
    a.is_favorite,
    CASE
        WHEN j.items_expected > 0
        THEN MIN(100, CAST(ROUND(100.0 * (j.items_downloaded + j.items_skipped) / j.items_expected) AS INTEGER))
        ELSE NULL
    END AS percent_complete
FROM scrape_jobs j
LEFT JOIN accounts a ON a.id = j.account_id;

-- ---------------------------------------------------------------------------
-- v_archive_stats — single-row summary for the dashboard header.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_archive_stats;
CREATE VIEW v_archive_stats AS
SELECT
    (SELECT COUNT(*) FROM accounts)                                        AS account_count,
    (SELECT COUNT(*) FROM accounts WHERE status = 'active')                AS active_count,
    (SELECT COUNT(*) FROM accounts WHERE status = 'legacy')                AS legacy_count,
    (SELECT COUNT(*) FROM accounts WHERE status = 'flagged')               AS flagged_count,
    (SELECT COUNT(*) FROM accounts WHERE is_favorite = 1)                  AS favorite_count,
    (SELECT COUNT(*) FROM accounts WHERE scrape_enabled = 1)               AS scrape_enabled_count,
    (SELECT COALESCE(SUM(image_count), 0) FROM accounts)                   AS image_count,
    (SELECT COALESCE(SUM(video_count), 0) FROM accounts)                   AS video_count,
    (SELECT COALESCE(SUM(total_bytes), 0) FROM accounts)                   AS total_bytes,
    (SELECT COUNT(*) FROM scrape_jobs WHERE status IN ('queued', 'deferred')) AS queued_jobs,
    (SELECT COUNT(*) FROM scrape_jobs WHERE status IN ('claimed', 'running'))  AS running_jobs,
    (SELECT COUNT(*) FROM event_log
      WHERE level IN ('error', 'critical') AND resolved_at IS NULL)        AS open_errors,
    (SELECT COUNT(*) FROM worker_heartbeats
      WHERE beat_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-90 seconds')) AS live_workers;

INSERT OR IGNORE INTO schema_migrations (version) VALUES ('0003_views');
