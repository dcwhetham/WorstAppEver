"""Job queue endpoints.

The backend never talks to the scraper. It writes queue rows and reads progress
back out, so a dead scraper surfaces as "jobs queued, worker offline" instead of
a failing dashboard.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..db import transaction
from ..deps import Conn
from ..models import JobRecord, WorkerStatus
from ..repositories import jobs as jobs_repo

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRecord])
def list_jobs(
    conn: Conn,
    account_id: int | None = None,
    status: str | None = Query(None, pattern="^(queued|claimed|running|succeeded|failed|cancelled|deferred)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    # Opportunistic reaping: cheap, and it means a crashed worker's jobs unstick
    # themselves the moment anyone opens the queue view.
    with transaction(conn):
        jobs_repo.reap_expired_leases(conn)
    return jobs_repo.list_jobs(conn, account_id=account_id, status=status, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobRecord)
def get_job(conn: Conn, job_id: int) -> dict[str, Any]:
    job = jobs_repo.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(conn: Conn, job_id: int) -> dict[str, Any]:
    """Cancel a job.

    Queued jobs are cancelled outright. A running job is only *asked* to stop:
    the worker holds the lease and checks the flag at its next pacing pause, so
    it can finish or discard the in-flight file cleanly.
    """
    job = jobs_repo.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")

    with transaction(conn):
        if jobs_repo.cancel(conn, job_id):
            return {"status": "cancelled", "job_id": job_id}
        if jobs_repo.request_cancel_running(conn, job_id):
            return {"status": "cancel_requested", "job_id": job_id}
    return {"status": "not_cancellable", "job_id": job_id, "job_status": job["status"]}


@router.post("/{job_id}/retry", status_code=202)
def retry_job(conn: Conn, job_id: int) -> dict[str, Any]:
    with transaction(conn):
        new_id = jobs_repo.retry(conn, job_id)
    if new_id is None:
        raise HTTPException(status_code=400, detail="job cannot be retried (missing or not account-scoped)")
    return {"job_id": new_id, "retried_from": job_id}


workers_router = APIRouter(prefix="/workers", tags=["workers"])


@workers_router.get("", response_model=list[WorkerStatus])
def list_workers(conn: Conn) -> list[dict[str, Any]]:
    """Scraper liveness, read from heartbeats rather than by pinging the container."""
    return jobs_repo.workers(conn)
