"""HTTP Range support for serving raw media.

`<video>` will not let you seek unless the server answers Range requests with
206 responses — without this, Chrome downloads the whole file before it plays
and the scrubber does nothing. Since the whole point of keeping files raw is
direct playback, range handling is a requirement, not an optimisation.

Implemented explicitly rather than relying on the framework's file response,
whose range behaviour varies by version.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse

CHUNK_SIZE = 512 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

# Extensions the stdlib map gets wrong or misses, which matters because the
# browser picks its player from the Content-Type.
_EXTRA_TYPES = {
    ".mkv": "video/x-matroska",
    ".m4v": "video/x-m4v",
    ".ts": "video/mp2t",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


def guess_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _iter_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining > 0:
            block = handle.read(min(CHUNK_SIZE, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def ranged_file_response(
    path: Path,
    request: Request,
    *,
    filename: str | None = None,
    download: bool = False,
    cache_seconds: int = 3600,
) -> Response:
    size = path.stat().st_size
    media_type = guess_media_type(path)
    disposition = "attachment" if download else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{filename or path.name}"',
        # Content is immutable per media id: a changed file gets a new hash and
        # is re-indexed rather than mutated in place.
        "Cache-Control": f"private, max-age={cache_seconds}",
    }

    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=media_type, headers=headers)

    match = _RANGE_RE.fullmatch(range_header.strip())
    if not match:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})

    raw_start, raw_end = match.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    elif raw_end:
        # Suffix form "bytes=-500": the trailing N bytes.
        start = max(0, size - int(raw_end))
        end = size - 1
    else:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})

    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})

    return StreamingResponse(
        _iter_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )
