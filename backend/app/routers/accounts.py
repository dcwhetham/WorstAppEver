"""Account endpoints: the dashboard grid, the expanded view, and card actions."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Response
from starlette.responses import FileResponse

from .. import bundler
from ..db import transaction
from ..deps import Conn
from ..links import add_manual_link, ensure_derived_links
from ..models import (
    AccountCard,
    AccountCreate,
    AccountDetail,
    AccountLink,
    AccountUpdate,
    BatchJobCreate,
    BatchUpdate,
    JobCreate,
    LinkCreate,
)
from ..repositories import accounts as accounts_repo
from ..repositories import jobs as jobs_repo
from ..repositories import media as media_repo

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _require(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    card = accounts_repo.get_card(conn, account_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found")
    return card


@router.get("", response_model=list[AccountCard])
def list_accounts(
    conn: Conn,
    response: Response,
    q: str | None = Query(None, description="Substring match on name or display name"),
    favorite: bool | None = None,
    status: str | None = Query(None, pattern="^(active|legacy|flagged)$"),
    scrape_enabled: bool | None = None,
    has_errors: bool | None = None,
    sort: str = Query("name", pattern="^(name|recent|added|media|size|errors|backlog)$"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """The dashboard grid. Favourites always sort first, whatever `sort` says."""
    cards, total = accounts_repo.list_cards(
        conn,
        search=q,
        favorite=favorite,
        status=status,
        scrape_enabled=scrape_enabled,
        has_errors=has_errors,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    # Total goes in a header so the response body stays a plain array and the
    # client can render straight from it.
    response.headers["X-Total-Count"] = str(total)
    return cards


@router.post("", response_model=AccountDetail, status_code=201)
def create_account(conn: Conn, payload: AccountCreate) -> dict[str, Any]:
    existing = accounts_repo.get_by_name(conn, payload.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"account '{payload.name}' already exists")

    with transaction(conn):
        account_id = accounts_repo.create(conn, payload)
        # A brand new account gets its first sync queued immediately; the
        # scraper's ramping logic spreads that first pull over several runs.
        if payload.scrape_enabled:
            jobs_repo.enqueue(conn, account_id, job_type="sync", trigger="bootstrap")

    return _detail(conn, account_id)


def _detail(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    card = _require(conn, account_id)
    recent_media, _ = media_repo.list_for_account(conn, account_id, limit=24)
    return {
        **card,
        "links": accounts_repo.list_links(conn, account_id),
        "recent_media": recent_media,
        "recent_jobs": jobs_repo.list_jobs(conn, account_id=account_id, limit=10),
    }


@router.get("/{account_id}", response_model=AccountDetail)
def get_account(conn: Conn, account_id: int) -> dict[str, Any]:
    return _detail(conn, account_id)


@router.patch("/{account_id}", response_model=AccountCard)
def update_account(conn: Conn, account_id: int, patch: AccountUpdate) -> dict[str, Any]:
    _require(conn, account_id)
    with transaction(conn):
        accounts_repo.update(conn, account_id, patch)
    return _require(conn, account_id)


@router.delete("/{account_id}", status_code=204)
def delete_account(conn: Conn, account_id: int) -> None:
    _require(conn, account_id)
    with transaction(conn):
        accounts_repo.delete(conn, account_id)


# --------------------------------------------------------------------------
# Quick actions
# --------------------------------------------------------------------------


@router.post("/{account_id}/favorite", response_model=AccountCard)
def toggle_favorite(
    conn: Conn, account_id: int, value: Annotated[bool | None, Body(embed=True)] = None
) -> dict[str, Any]:
    """Set the favourite star, or flip it when `value` is omitted."""
    card = _require(conn, account_id)
    target = (not card["is_favorite"]) if value is None else value
    with transaction(conn):
        accounts_repo.update(conn, account_id, AccountUpdate(is_favorite=target))
    return _require(conn, account_id)


@router.post("/{account_id}/scrape-toggle", response_model=AccountCard)
def toggle_scrape(
    conn: Conn, account_id: int, value: Annotated[bool | None, Body(embed=True)] = None
) -> dict[str, Any]:
    card = _require(conn, account_id)
    target = (not card["scrape_enabled"]) if value is None else value
    with transaction(conn):
        accounts_repo.update(conn, account_id, AccountUpdate(scrape_enabled=target))
        if not target and card.get("active_job") and card["active_job"]["status"] in {"queued", "deferred"}:
            jobs_repo.cancel(conn, card["active_job"]["id"])
    return _require(conn, account_id)


@router.post("/{account_id}/run", status_code=202)
def run_now(conn: Conn, account_id: int, payload: JobCreate | None = None) -> dict[str, Any]:
    """ "Run Now": enqueue an on-demand job.

    Returns 202 with `created=false` when a job is already pending, rather than
    an error — from the user's point of view the request succeeded either way.
    """
    _require(conn, account_id)
    options = payload or JobCreate()
    with transaction(conn):
        job_id, created = jobs_repo.enqueue(
            conn,
            account_id,
            job_type=options.job_type,
            trigger=options.trigger,
            priority=1000 if options.force else options.priority,
            payload=options.payload,
            requested_by="web",
        )
    return {"job_id": job_id, "created": created, "job": jobs_repo.get_job(conn, job_id)}


@router.get("/{account_id}/bundle")
def bundle_account(
    conn: Conn,
    account_id: int,
    media_type: str | None = Query(None, pattern="^(image|video)$"),
) -> FileResponse:
    """ "Bundle": zip the account folder and hand it to the browser."""
    _require(conn, account_id)
    try:
        with transaction(conn):
            result = bundler.build_bundle(conn, account_id, media_type=media_type)
    except bundler.BundleTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    if result.file_count == 0:
        raise HTTPException(status_code=404, detail="nothing to bundle: no indexed files for this account")

    return FileResponse(
        result.path,
        media_type="application/zip",
        filename=result.filename,
        headers={
            "X-Bundle-File-Count": str(result.file_count),
            "X-Bundle-Cached": "1" if result.from_cache else "0",
        },
    )


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------


@router.get("/{account_id}/links", response_model=list[AccountLink])
def get_links(conn: Conn, account_id: int) -> list[dict[str, Any]]:
    _require(conn, account_id)
    return accounts_repo.list_links(conn, account_id)


@router.post("/{account_id}/links", response_model=AccountLink, status_code=201)
def create_link(conn: Conn, account_id: int, payload: LinkCreate) -> dict[str, Any]:
    _require(conn, account_id)
    with transaction(conn):
        link_id = add_manual_link(
            conn,
            account_id,
            payload.url,
            provider=payload.provider,
            label=payload.label,
            remote_handle=payload.remote_handle,
            sort_order=payload.sort_order,
        )
    if link_id is None:
        raise HTTPException(status_code=409, detail="that link is already registered for this account")
    row = next(link for link in accounts_repo.list_links(conn, account_id) if link["id"] == link_id)
    return row


@router.delete("/{account_id}/links/{link_id}", status_code=204)
def delete_link(conn: Conn, account_id: int, link_id: int) -> None:
    with transaction(conn):
        cursor = conn.execute(
            "DELETE FROM account_links WHERE id = ? AND account_id = ?", (link_id, account_id)
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="link not found")


@router.post("/{account_id}/links/rederive", response_model=list[AccountLink])
def rederive_links(conn: Conn, account_id: int) -> list[dict[str, Any]]:
    """Recreate the standard provider links. Manual entries are left untouched."""
    card = _require(conn, account_id)
    with transaction(conn):
        ensure_derived_links(conn, account_id, card["name"])
    return accounts_repo.list_links(conn, account_id)


# --------------------------------------------------------------------------
# Batch operations (selection mode in the UI)
# --------------------------------------------------------------------------

batch_router = APIRouter(prefix="/batch", tags=["batch"])


@batch_router.post("/run", status_code=202)
def batch_run(conn: Conn, payload: BatchJobCreate) -> dict[str, Any]:
    """Bulk scrape. Jobs are staggered so a 40-account batch is not a traffic spike."""
    with transaction(conn):
        return jobs_repo.enqueue_batch(
            conn, payload.account_ids, job_type=payload.job_type, priority=payload.priority
        )


@batch_router.patch("/accounts")
def batch_update(conn: Conn, payload: BatchUpdate) -> dict[str, int]:
    with transaction(conn):
        updated = accounts_repo.bulk_update(conn, payload.account_ids, payload.patch)
    return {"updated": updated}


@batch_router.post("/bundle")
def batch_bundle(conn: Conn, account_ids: Annotated[list[int], Body(embed=True)]) -> dict[str, Any]:
    """Prepare bundles for several accounts and return their download URLs.

    Deliberately not one combined zip: multi-account bundles routinely exceed
    what a browser will hold, and separate files let a failure affect one
    account instead of the whole batch.
    """
    prepared: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for account_id in account_ids:
        try:
            with transaction(conn):
                result = bundler.build_bundle(conn, account_id)
            prepared.append(
                {
                    "account_id": account_id,
                    "filename": result.filename,
                    "size_bytes": result.size_bytes,
                    "file_count": result.file_count,
                    "download_url": f"/api/accounts/{account_id}/bundle",
                }
            )
        except (bundler.BundleTooLargeError, LookupError, OSError) as exc:
            failed.append({"account_id": account_id, "error": str(exc)})
    return {"prepared": prepared, "failed": failed}
