"""Zip an account's archive folder for browser download.

Two decisions worth keeping:

**ZIP_STORED, not DEFLATE.** The archive is JPEG and H.264 — already entropy
coded. Deflating it burns CPU for roughly zero size reduction and, worse, makes
the output size unpredictable. Stored entries let us set a real `Content-Length`
so the browser shows an honest progress bar instead of a spinner.

**Content-addressed cache filenames.** The bundle name embeds a digest of the
account's file inventory, so an unchanged account reuses the existing zip and a
changed one misses the cache automatically. No invalidation logic, no stale
downloads.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, get_settings
from .logs import log_event
from .util import sanitize_filename

# Above this, a synchronous request-scoped zip is the wrong tool; the API
# returns 413 and points at the per-folder download instead.
MAX_BUNDLE_BYTES = 20 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class Bundle:
    path: Path
    filename: str
    size_bytes: int
    file_count: int
    from_cache: bool


class BundleTooLargeError(RuntimeError):
    def __init__(self, size_bytes: int) -> None:
        super().__init__(f"archive is {size_bytes} bytes, above the {MAX_BUNDLE_BYTES} byte bundle limit")
        self.size_bytes = size_bytes


def _inventory(conn: sqlite3.Connection, account_id: int) -> tuple[list[tuple[str, int]], str, int]:
    """Present files for an account, plus a digest identifying that exact set."""
    rows = conn.execute(
        """
        SELECT rel_path, bytes, mtime_ns
          FROM media_files
         WHERE account_id = ? AND deleted_at IS NULL AND is_missing = 0
         ORDER BY rel_path
        """,
        (account_id,),
    ).fetchall()

    digest = hashlib.sha256()
    files: list[tuple[str, int]] = []
    total = 0
    for row in rows:
        digest.update(f"{row['rel_path']}:{row['bytes']}:{row['mtime_ns']}".encode())
        files.append((row["rel_path"], int(row["bytes"] or 0)))
        total += int(row["bytes"] or 0)
    return files, digest.hexdigest()[:16], total


def build_bundle(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    settings: Settings | None = None,
    media_type: str | None = None,
) -> Bundle:
    settings = settings or get_settings()
    row = conn.execute("SELECT name, archive_path FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise LookupError(f"account {account_id} not found")

    account_name = row["name"]
    account_dir = settings.account_dir(account_name, row["archive_path"])
    files, inventory_hash, total_bytes = _inventory(conn, account_id)

    if media_type:
        prefix = settings.photos_dir if media_type == "image" else settings.videos_dir
        files = [(rel, size) for rel, size in files if rel.startswith(prefix + "/")]
        total_bytes = sum(size for _, size in files)
        inventory_hash = f"{inventory_hash}-{media_type}"

    if total_bytes > MAX_BUNDLE_BYTES:
        raise BundleTooLargeError(total_bytes)

    safe_name = sanitize_filename(account_name, "account")
    download_name = f"{safe_name}-archive.zip"
    settings.bundle_cache_dir.mkdir(parents=True, exist_ok=True)
    target = settings.bundle_cache_dir / f"{safe_name}-{inventory_hash}.zip"

    if target.exists() and target.stat().st_size > 0:
        return Bundle(target, download_name, target.stat().st_size, len(files), True)

    # Write to a temp name and rename, so a crash mid-zip cannot leave a
    # truncated file sitting at a cache-hit path.
    staging = target.with_suffix(".zip.partial")
    written = 0
    with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for rel_path, _ in files:
            source = account_dir.joinpath(*rel_path.split("/"))
            if not source.is_file():
                continue
            archive.write(source, arcname=f"{safe_name}/{rel_path}")
            written += 1
    staging.replace(target)

    _prune_cache(settings, keep=12)
    log_event(
        conn,
        level="info",
        source="backend",
        event="bundle_created",
        message=f"Bundled {written} files ({total_bytes} bytes) for '{account_name}'",
        account_id=account_id,
        detail={"file_count": written, "bytes": total_bytes, "media_type": media_type},
    )
    return Bundle(target, download_name, target.stat().st_size, written, False)


def _prune_cache(settings: Settings, keep: int = 12) -> None:
    """Keep the cache bounded; bundles are reproducible, so eviction is cheap."""
    zips = sorted(
        settings.bundle_cache_dir.glob("*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in zips[keep:]:
        stale.unlink(missing_ok=True)
