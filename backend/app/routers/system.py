"""System endpoints: health, stats, settings, and the archive scanner."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..config import get_settings
from ..db import connect, transaction
from ..deps import Conn
from ..models import ArchiveStats, ScanRequest
from ..repositories import jobs as jobs_repo
from ..scanner import scan_archive

router = APIRouter(tags=["system"])

_CASTS = {
    "int": int,
    "float": float,
    "bool": lambda v: str(v).lower() in {"1", "true", "yes", "on"},
}


@router.get("/health")
def health(conn: Conn) -> dict[str, Any]:
    """Liveness plus enough detail to diagnose a half-broken deployment.

    Reports the archive root's presence separately from the database: a missing
    bind mount is the single most common misconfiguration, and it otherwise
    manifests as a scan that mysteriously finds nothing.
    """
    settings = get_settings()
    migrations = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    live = [w for w in jobs_repo.workers(conn) if w["is_alive"]]
    return {
        "status": "ok",
        "database": str(settings.db_path),
        "migrations_applied": int(migrations),
        "archive_root": str(settings.archive_root),
        "archive_root_present": settings.archive_root.is_dir(),
        "scraper_workers_online": len(live),
    }


@router.get("/stats", response_model=ArchiveStats)
def stats(conn: Conn) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM v_archive_stats").fetchone()
    return dict(row)


@router.get("/settings")
def get_runtime_settings(conn: Conn) -> dict[str, Any]:
    """Runtime knobs, typed. The scraper reads these instead of its own config."""
    rows = conn.execute("SELECT key, value, value_type, description FROM settings ORDER BY key").fetchall()
    out: dict[str, Any] = {}
    for row in rows:
        caster = _CASTS.get(row["value_type"])
        try:
            value = caster(row["value"]) if caster else row["value"]
        except (TypeError, ValueError):
            value = row["value"]
        out[row["key"]] = {"value": value, "type": row["value_type"], "description": row["description"]}
    return out


@router.patch("/settings")
def update_runtime_settings(conn: Conn, patch: dict[str, Any]) -> dict[str, Any]:
    """Update known keys only.

    Unknown keys are rejected rather than created: a typo that silently inserts
    `scraper.min_delay_msec` would leave the real setting untouched and look like
    the change had no effect.
    """
    known = {row["key"] for row in conn.execute("SELECT key FROM settings")}
    unknown = set(patch) - known
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown settings: {sorted(unknown)}")

    with transaction(conn):
        for key, value in patch.items():
            stored = "true" if value is True else "false" if value is False else str(value)
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (stored, key))
    return get_runtime_settings(conn)


def _run_scan(account_id: int | None, rehash: bool) -> None:
    """Scan on a fresh connection, since the request's is closed by then."""
    conn = connect()
    try:
        scan_archive(conn=conn, account_id=account_id, rehash=rehash)
    finally:
        conn.close()


@router.post("/scan", status_code=202)
def trigger_scan(payload: ScanRequest, background: BackgroundTasks) -> dict[str, Any]:
    """Reconcile `/archive` with the index.

    Runs in the background: hashing a large archive takes minutes and would blow
    any sensible HTTP timeout. Progress lands in `event_log`, so the UI follows
    it through the same log viewer as everything else.
    """
    background.add_task(_run_scan, payload.account_id, payload.rehash)
    return {
        "status": "scanning",
        "account_id": payload.account_id,
        "rehash": payload.rehash,
        "follow": "/api/logs?source=scanner",
    }


@router.post("/scan/sync")
def trigger_scan_sync(payload: ScanRequest, conn: Conn) -> dict[str, Any]:
    """Blocking scan, for scripts and tests that need the report back."""
    report = scan_archive(conn=conn, account_id=payload.account_id, rehash=payload.rehash)
    return report.as_dict()
