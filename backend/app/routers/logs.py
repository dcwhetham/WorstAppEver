"""Log endpoints backing the per-account error/log viewer modal."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..db import transaction
from ..deps import Conn
from ..logs import prune_event_log, resolve_account_errors
from ..models import LogEntry

router = APIRouter(tags=["logs"])


def _decode(row: Any) -> dict[str, Any]:
    data = dict(row)
    if data.get("detail"):
        try:
            data["detail"] = json.loads(data["detail"])
        except (TypeError, ValueError):
            # Keep a malformed detail blob visible rather than dropping the row;
            # a log viewer that hides broken logs is worse than useless.
            data["detail"] = {"raw": data["detail"]}
    data["retryable"] = bool(data.get("retryable"))
    return data


def query_logs(
    conn: Any,
    *,
    account_id: int | None = None,
    job_id: int | None = None,
    level: str | None = None,
    source: str | None = None,
    unresolved_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Plain query helper.

    Kept separate from the endpoint so other routes can reuse it: calling an
    endpoint function directly would pass FastAPI `Query` objects through as
    parameter values.
    """
    where: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        where.append("account_id = ?")
        params.append(account_id)
    if job_id is not None:
        where.append("job_id = ?")
        params.append(job_id)
    if level:
        # Treat `level` as a floor, so filtering to "warn" also shows errors.
        order = ["debug", "info", "warn", "error", "critical"]
        allowed = order[order.index(level) :]
        where.append(f"level IN ({','.join('?' * len(allowed))})")
        params.extend(allowed)
    if source:
        where.append("source = ?")
        params.append(source)
    if unresolved_only:
        where.append("resolved_at IS NULL")

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM event_log {clause} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [_decode(row) for row in rows]


@router.get("/logs", response_model=list[LogEntry])
def list_logs(
    conn: Conn,
    account_id: int | None = None,
    job_id: int | None = None,
    level: str | None = Query(None, pattern="^(debug|info|warn|error|critical)$"),
    source: str | None = Query(None, pattern="^(backend|scraper|scheduler|scanner|web)$"),
    unresolved_only: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return query_logs(
        conn,
        account_id=account_id,
        job_id=job_id,
        level=level,
        source=source,
        unresolved_only=unresolved_only,
        limit=limit,
        offset=offset,
    )


@router.get("/accounts/{account_id}/logs", response_model=list[LogEntry])
def account_logs(
    conn: Conn,
    account_id: int,
    level: str | None = Query(None, pattern="^(debug|info|warn|error|critical)$"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Per-account log feed behind the card's log viewer modal."""
    if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found")
    return query_logs(conn, account_id=account_id, level=level, limit=limit)


@router.post("/accounts/{account_id}/logs/resolve")
def resolve_logs(conn: Conn, account_id: int) -> dict[str, int]:
    """Dismiss an account's open errors, clearing its card badge."""
    with transaction(conn):
        resolved = resolve_account_errors(conn, account_id)
    return {"resolved": resolved}


@router.post("/logs/prune")
def prune_logs(conn: Conn, keep_days: int = Query(90, ge=1, le=3650)) -> dict[str, int]:
    """Drop old resolved entries. Unresolved errors are never pruned."""
    with transaction(conn):
        deleted = prune_event_log(conn, keep_days=keep_days)
    return {"deleted": deleted}
