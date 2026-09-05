-- ---------------------------------------------------------------------------
-- 0002_triggers.sql — keep denormalised counters and timestamps honest.
--
-- Counters are maintained with incremental deltas rather than a recomputing
-- aggregate: a local scan inserts thousands of media rows in one transaction,
-- and a "SELECT COUNT(*)" trigger would turn that into quadratic work.
-- ---------------------------------------------------------------------------

-- --------------------------- media_files -> accounts ------------------------

DROP TRIGGER IF EXISTS trg_media_after_insert;
CREATE TRIGGER trg_media_after_insert
AFTER INSERT ON media_files
WHEN NEW.deleted_at IS NULL
BEGIN
    UPDATE accounts SET
        image_count = image_count + (CASE WHEN NEW.media_type = 'image' THEN 1 ELSE 0 END),
        video_count = video_count + (CASE WHEN NEW.media_type = 'video' THEN 1 ELSE 0 END),
        other_count = other_count + (CASE WHEN NEW.media_type = 'other' THEN 1 ELSE 0 END),
        total_bytes = total_bytes + COALESCE(NEW.bytes, 0),
        last_download_at = CASE
            WHEN NEW.downloaded_at IS NOT NULL
                 AND (last_download_at IS NULL OR NEW.downloaded_at > last_download_at)
            THEN NEW.downloaded_at ELSE last_download_at END,
        last_import_at = CASE
            WHEN NEW.imported_at IS NOT NULL
                 AND (last_import_at IS NULL OR NEW.imported_at > last_import_at)
            THEN NEW.imported_at ELSE last_import_at END
    WHERE id = NEW.account_id;
END;

DROP TRIGGER IF EXISTS trg_media_after_delete;
CREATE TRIGGER trg_media_after_delete
AFTER DELETE ON media_files
WHEN OLD.deleted_at IS NULL
BEGIN
    UPDATE accounts SET
        image_count = MAX(0, image_count - (CASE WHEN OLD.media_type = 'image' THEN 1 ELSE 0 END)),
        video_count = MAX(0, video_count - (CASE WHEN OLD.media_type = 'video' THEN 1 ELSE 0 END)),
        other_count = MAX(0, other_count - (CASE WHEN OLD.media_type = 'other' THEN 1 ELSE 0 END)),
        total_bytes = MAX(0, total_bytes - COALESCE(OLD.bytes, 0))
    WHERE id = OLD.account_id;
END;

-- Handles soft-delete, undelete, retyping and size corrections in one pass.
DROP TRIGGER IF EXISTS trg_media_after_update;
CREATE TRIGGER trg_media_after_update
AFTER UPDATE ON media_files
WHEN OLD.media_type  IS NOT NEW.media_type
  OR OLD.bytes       IS NOT NEW.bytes
  OR OLD.deleted_at  IS NOT NEW.deleted_at
  OR OLD.account_id  IS NOT NEW.account_id
BEGIN
    UPDATE accounts SET
        image_count = MAX(0, image_count - (CASE WHEN OLD.deleted_at IS NULL AND OLD.media_type = 'image' THEN 1 ELSE 0 END)),
        video_count = MAX(0, video_count - (CASE WHEN OLD.deleted_at IS NULL AND OLD.media_type = 'video' THEN 1 ELSE 0 END)),
        other_count = MAX(0, other_count - (CASE WHEN OLD.deleted_at IS NULL AND OLD.media_type = 'other' THEN 1 ELSE 0 END)),
        total_bytes = MAX(0, total_bytes - (CASE WHEN OLD.deleted_at IS NULL THEN COALESCE(OLD.bytes, 0) ELSE 0 END))
    WHERE id = OLD.account_id;

    UPDATE accounts SET
        image_count = image_count + (CASE WHEN NEW.deleted_at IS NULL AND NEW.media_type = 'image' THEN 1 ELSE 0 END),
        video_count = video_count + (CASE WHEN NEW.deleted_at IS NULL AND NEW.media_type = 'video' THEN 1 ELSE 0 END),
        other_count = other_count + (CASE WHEN NEW.deleted_at IS NULL AND NEW.media_type = 'other' THEN 1 ELSE 0 END),
        total_bytes = total_bytes + (CASE WHEN NEW.deleted_at IS NULL THEN COALESCE(NEW.bytes, 0) ELSE 0 END)
    WHERE id = NEW.account_id;
END;

-- ------------------------------- updated_at ---------------------------------
-- The WHEN guards make these no-ops when a caller sets updated_at explicitly,
-- which also keeps them safe if recursive_triggers is ever turned on.

DROP TRIGGER IF EXISTS trg_accounts_touch;
CREATE TRIGGER trg_accounts_touch
AFTER UPDATE ON accounts
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE accounts SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_jobs_touch;
CREATE TRIGGER trg_jobs_touch
AFTER UPDATE ON scrape_jobs
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE scrape_jobs SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_remote_touch;
CREATE TRIGGER trg_remote_touch
AFTER UPDATE ON remote_index
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE remote_index SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS trg_settings_touch;
CREATE TRIGGER trg_settings_touch
AFTER UPDATE OF value ON settings
BEGIN
    UPDATE settings SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE key = NEW.key;
END;

-- ------------------------- job outcome -> account state ---------------------
-- Terminal job transitions roll straight into the account row so the card can
-- render "last synced / failing since" without joining the job table.

DROP TRIGGER IF EXISTS trg_job_success;
CREATE TRIGGER trg_job_success
AFTER UPDATE OF status ON scrape_jobs
WHEN NEW.status = 'succeeded' AND OLD.status <> 'succeeded' AND NEW.account_id IS NOT NULL
BEGIN
    UPDATE accounts SET
        last_scrape_at       = COALESCE(NEW.finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        last_success_at      = COALESCE(NEW.finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        consecutive_failures = 0
    WHERE id = NEW.account_id;

    -- A successful run clears stale error badges for that account.
    UPDATE event_log SET resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE account_id = NEW.account_id
      AND resolved_at IS NULL
      AND level IN ('error', 'critical');
END;

DROP TRIGGER IF EXISTS trg_job_failure;
CREATE TRIGGER trg_job_failure
AFTER UPDATE OF status ON scrape_jobs
WHEN NEW.status = 'failed' AND OLD.status <> 'failed' AND NEW.account_id IS NOT NULL
BEGIN
    UPDATE accounts SET
        last_scrape_at       = COALESCE(NEW.finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        last_error_at        = COALESCE(NEW.finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        consecutive_failures = consecutive_failures + 1
    WHERE id = NEW.account_id;
END;

INSERT OR IGNORE INTO schema_migrations (version) VALUES ('0002_triggers');
