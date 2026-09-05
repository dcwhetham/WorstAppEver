"""Media endpoints — the gallery listing and raw byte delivery.

`/raw` serves the untouched file with Range support. There is no thumbnail
pipeline and no transcode step: "files remain completely raw for direct local
viewing" is the product requirement, and a derived-asset cache would be the
first thing to drift out of sync with the archive.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..config import get_settings
from ..db import transaction
from ..deps import Conn
from ..models import MediaPage
from ..repositories import media as media_repo
from ..streaming import ranged_file_response

router = APIRouter(tags=["media"])


@router.get("/accounts/{account_id}/media", response_model=MediaPage)
def list_media(
    conn: Conn,
    account_id: int,
    media_type: str | None = Query(None, pattern="^(image|video|other)$"),
    include_missing: bool = False,
    sort: str = Query("newest", pattern="^(newest|oldest|name|size)$"),
    limit: int = Query(120, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    exists = conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found")

    items, total = media_repo.list_for_account(
        conn,
        account_id,
        media_type=media_type,
        include_missing=include_missing,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/media/{media_id}/raw")
def get_raw_media(conn: Conn, media_id: int, request: Request, download: bool = False) -> Response:
    row = media_repo.get(conn, media_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"media {media_id} not found")

    try:
        path = media_repo.resolve_path(row, get_settings())
    except ValueError as exc:
        # safe_join rejected the stored path. Treat as data corruption, not a
        # client error, and never fall back to serving it anyway.
        raise HTTPException(status_code=500, detail=f"invalid stored path: {exc}") from exc

    if not path.is_file():
        # Flag it so the next scan does not have to be the one to notice, and so
        # the UI can grey the tile out immediately.
        with transaction(conn):
            conn.execute("UPDATE media_files SET is_missing = 1 WHERE id = ?", (media_id,))
        raise HTTPException(status_code=410, detail="file is indexed but no longer on disk")

    return ranged_file_response(path, request, filename=row["filename"], download=download)


@router.get("/media/duplicates")
def list_duplicates(conn: Conn, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    """Groups of identical bytes in the archive, within or across accounts.

    Reported, never auto-resolved: the same image held by two accounts is
    usually intentional, and pruning is the user's call. Each group lists its
    members so that call can be made against a file.
    """
    return {"groups": media_repo.duplicate_report(conn, limit)}


@router.delete("/media/{media_id}", status_code=204)
def forget_media(conn: Conn, media_id: int) -> None:
    """Tombstone a media row so the scraper stops trying to re-fetch it.

    Does not touch the file. Removing bytes stays a deliberate manual act.
    """
    with transaction(conn):
        if not media_repo.soft_delete(conn, media_id):
            raise HTTPException(status_code=404, detail=f"media {media_id} not found")
