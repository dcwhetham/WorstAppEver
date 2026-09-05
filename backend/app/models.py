"""Pydantic request/response models — the wire contract for the Web GUI.

Field names match the SQL columns so a `sqlite3.Row` maps straight through
without a translation layer. The one place that shape diverges is
`AccountCard.active_job` / `.last_error`, where the flat columns from
`v_account_dashboard` are nested for the client.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .util import is_valid_account_name

AccountStatus = Literal["active", "legacy", "flagged"]
PlatformState = Literal["unknown", "ok", "private", "unavailable", "banned"]
MediaType = Literal["image", "video", "other"]
LinkProvider = Literal["instagram", "imginn", "pixnoy", "picuki", "dumpor", "sotwe", "twitter", "custom"]
JobType = Literal["sync", "discover", "backfill", "verify", "probe"]
JobStatus = Literal["queued", "claimed", "running", "succeeded", "failed", "cancelled", "deferred"]
LogLevel = Literal["debug", "info", "warn", "error", "critical"]

AccountName = Annotated[str, Field(min_length=1, max_length=64)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


class AccountCreate(BaseModel):
    name: AccountName
    display_name: str | None = None
    platform: str = "instagram"
    status: AccountStatus = "active"
    is_favorite: bool = False
    scrape_enabled: bool = True
    priority: int = 0
    notes: str | None = None
    # Optional extra mirrors to register alongside the derived provider links.
    links: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip().lstrip("@")
        if not is_valid_account_name(value):
            raise ValueError("account name may only contain letters, digits, dot, underscore and hyphen")
        return value


class AccountUpdate(BaseModel):
    """All-optional patch. `None` means "leave alone", not "set to null"."""

    display_name: str | None = None
    status: AccountStatus | None = None
    platform_state: PlatformState | None = None
    is_favorite: bool | None = None
    scrape_enabled: bool | None = None
    priority: int | None = None
    notes: str | None = None
    expected_image_count: int | None = None
    expected_video_count: int | None = None


class LinkCreate(BaseModel):
    url: str = Field(min_length=4, max_length=1024)
    provider: LinkProvider | None = None  # inferred from the host when omitted
    label: str | None = None
    remote_handle: str | None = None
    sort_order: int = 100


class AccountLink(ORMModel):
    id: int
    account_id: int
    provider: str
    kind: str
    url: str
    label: str | None = None
    remote_handle: str | None = None
    is_enabled: bool
    sort_order: int
    last_ok_at: str | None = None
    last_error_at: str | None = None


class JobProgress(ORMModel):
    id: int
    status: JobStatus
    job_type: JobType
    phase: str | None = None
    items_expected: int = 0
    items_downloaded: int = 0
    items_skipped: int = 0
    items_failed: int = 0
    bytes_downloaded: int = 0
    eta_seconds: int | None = None
    pace_delay_ms: int | None = None
    started_at: str | None = None
    message: str | None = None
    percent_complete: int | None = None


class ErrorSummary(ORMModel):
    id: int
    ts: str
    level: LogLevel
    event: str
    message: str
    retryable: bool = False


class AccountCard(ORMModel):
    """One row of the dashboard grid."""

    id: int
    name: str
    display_name: str | None = None
    platform: str
    status: AccountStatus
    platform_state: PlatformState
    is_favorite: bool
    scrape_enabled: bool
    priority: int
    image_count: int
    video_count: int
    other_count: int
    media_count: int
    total_bytes: int
    expected_image_count: int | None = None
    expected_video_count: int | None = None
    last_download_at: str | None = None
    last_import_at: str | None = None
    last_scrape_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    consecutive_failures: int
    pending_remote_count: int = 0
    estimated_missing_count: int = 0
    unresolved_error_count: int = 0
    is_new: bool = False
    link_count: int = 0
    notes: str | None = None
    created_at: str
    updated_at: str

    active_job: JobProgress | None = None
    last_error: ErrorSummary | None = None
    # Relative URL of a representative image, or None for an empty account.
    cover_url: str | None = None


class AccountDetail(AccountCard):
    links: list[AccountLink] = Field(default_factory=list)
    recent_media: list[MediaItem] = Field(default_factory=list)
    recent_jobs: list[JobProgress] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------


class MediaItem(ORMModel):
    id: int
    account_id: int
    media_type: MediaType
    rel_path: str
    filename: str
    ext: str | None = None
    bytes: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    captured_at: str | None = None
    downloaded_at: str | None = None
    imported_at: str | None = None
    is_missing: bool = False
    content_hash: str | None = None
    # Raw bytes, streamed with range support. No transcode, no thumbnail cache.
    raw_url: str


class MediaPage(BaseModel):
    items: list[MediaItem]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Jobs / logs / system
# --------------------------------------------------------------------------


class JobCreate(BaseModel):
    job_type: JobType = "sync"
    priority: int | None = None
    trigger: Literal["manual", "batch"] = "manual"
    payload: dict[str, Any] | None = None
    # Push straight to the front of the queue, ignoring the scheduled block.
    force: bool = False


class BatchJobCreate(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=500)
    job_type: JobType = "sync"
    priority: int | None = None


class BatchUpdate(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=500)
    patch: AccountUpdate


class JobRecord(JobProgress):
    account_id: int | None = None
    account_name: str | None = None
    trigger: str
    priority: int
    attempts: int
    max_attempts: int
    scheduled_for: str
    claimed_by: str | None = None
    finished_at: str | None = None
    error_summary: str | None = None
    created_at: str
    updated_at: str


class LogEntry(ORMModel):
    id: int
    ts: str
    level: LogLevel
    source: str
    account_id: int | None = None
    job_id: int | None = None
    event: str
    message: str
    detail: dict[str, Any] | None = None
    error_type: str | None = None
    traceback: str | None = None
    occurrences: int = 1
    first_seen_at: str | None = None
    retryable: bool = False
    resolved_at: str | None = None


class WorkerStatus(ORMModel):
    worker_id: str
    kind: str
    hostname: str | None = None
    version: str | None = None
    status: str
    current_job_id: int | None = None
    beat_at: str
    started_at: str
    # Derived: heartbeat newer than the staleness window.
    is_alive: bool = False


class ArchiveStats(ORMModel):
    account_count: int
    active_count: int
    legacy_count: int
    flagged_count: int
    favorite_count: int
    scrape_enabled_count: int
    image_count: int
    video_count: int
    total_bytes: int
    queued_jobs: int
    running_jobs: int
    open_errors: int
    live_workers: int


class ScanRequest(BaseModel):
    account_id: int | None = None
    # Force a full re-hash instead of trusting size+mtime. Slow but definitive.
    rehash: bool = False


AccountDetail.model_rebuild()
