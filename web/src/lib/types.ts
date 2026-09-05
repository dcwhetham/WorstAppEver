/**
 * Mirrors the Pydantic models in `backend/app/models.py`.
 *
 * Hand-written rather than generated from the OpenAPI schema: the surface is
 * small, and a generated client would obscure exactly the field-level detail
 * (nullable timestamps, the two separate status columns) that the UI has to
 * reason about.
 */

export type AccountStatus = "active" | "legacy" | "flagged";
export type PlatformState = "unknown" | "ok" | "private" | "unavailable" | "banned";
export type MediaType = "image" | "video" | "other";
export type JobStatus =
  | "queued"
  | "claimed"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "deferred";
export type LogLevel = "debug" | "info" | "warn" | "error" | "critical";

export interface JobProgress {
  id: number;
  status: JobStatus;
  job_type: string;
  phase: string | null;
  items_expected: number;
  items_downloaded: number;
  items_skipped: number;
  items_failed: number;
  bytes_downloaded: number;
  /** Null until the worker has enough samples for an honest estimate. */
  eta_seconds: number | null;
  /** Current self-pacing delay, shown as "pacing 9.5s/item". */
  pace_delay_ms: number | null;
  started_at: string | null;
  message: string | null;
  percent_complete: number | null;
}

export interface ErrorSummary {
  id: number;
  ts: string;
  level: LogLevel;
  event: string;
  message: string;
  retryable: boolean;
}

export interface AccountCard {
  id: number;
  name: string;
  display_name: string | null;
  platform: string;
  /** Our editorial state. */
  status: AccountStatus;
  /** What the platform reports. Orthogonal to `status`. */
  platform_state: PlatformState;
  is_favorite: boolean;
  scrape_enabled: boolean;
  priority: number;
  image_count: number;
  video_count: number;
  other_count: number;
  media_count: number;
  total_bytes: number;
  expected_image_count: number | null;
  expected_video_count: number | null;
  last_download_at: string | null;
  last_import_at: string | null;
  last_scrape_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  consecutive_failures: number;
  /** Discovered remotely but not yet on disk. */
  pending_remote_count: number;
  estimated_missing_count: number;
  unresolved_error_count: number;
  /** Never successfully synced, so its first pull is being ramped. */
  is_new: boolean;
  link_count: number;
  notes: string | null;
  created_at: string;
  updated_at: string;
  active_job: JobProgress | null;
  last_error: ErrorSummary | null;
  cover_url: string | null;
}

export interface AccountLink {
  id: number;
  account_id: number;
  provider: string;
  /** `manual` links are user property and survive a re-derive. */
  kind: "primary" | "derived" | "manual";
  url: string;
  label: string | null;
  remote_handle: string | null;
  is_enabled: boolean;
  sort_order: number;
  last_ok_at: string | null;
  last_error_at: string | null;
}

export interface MediaItem {
  id: number;
  account_id: number;
  media_type: MediaType;
  rel_path: string;
  filename: string;
  ext: string | null;
  bytes: number;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  captured_at: string | null;
  downloaded_at: string | null;
  imported_at: string | null;
  is_missing: boolean;
  content_hash: string | null;
  raw_url: string;
}

export interface MediaPage {
  items: MediaItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AccountDetail extends AccountCard {
  links: AccountLink[];
  recent_media: MediaItem[];
  recent_jobs: JobProgress[];
}

export interface LogEntry {
  id: number;
  ts: string;
  level: LogLevel;
  source: string;
  account_id: number | null;
  job_id: number | null;
  event: string;
  message: string;
  detail: Record<string, unknown> | null;
  error_type: string | null;
  traceback: string | null;
  /** Repeat count; identical events inside the window coalesce into one row. */
  occurrences: number;
  first_seen_at: string | null;
  retryable: boolean;
  resolved_at: string | null;
}

export interface ArchiveStats {
  account_count: number;
  active_count: number;
  legacy_count: number;
  flagged_count: number;
  favorite_count: number;
  scrape_enabled_count: number;
  image_count: number;
  video_count: number;
  total_bytes: number;
  queued_jobs: number;
  running_jobs: number;
  open_errors: number;
  live_workers: number;
}

export interface WorkerStatus {
  worker_id: string;
  kind: string;
  hostname: string | null;
  version: string | null;
  status: string;
  current_job_id: number | null;
  detail: string | null;
  beat_at: string;
  started_at: string;
  is_alive: boolean;
}

export type SortKey = "name" | "recent" | "added" | "media" | "size" | "errors" | "backlog";

export interface AccountFilters {
  q: string;
  favorite: boolean;
  status: AccountStatus | "";
  hasErrors: boolean;
  sort: SortKey;
}

export const EMPTY_FILTERS: AccountFilters = {
  q: "",
  favorite: false,
  status: "",
  hasErrors: false,
  sort: "name",
};
