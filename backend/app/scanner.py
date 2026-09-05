"""Local filesystem scanner.

Walks `/archive` and reconciles it with `media_files`. The filesystem is always
the source of truth: if a file is on disk it belongs in the index, and if it is
gone the row is marked missing rather than deleted, so the scraper does not
cheerfully re-download something the user removed on purpose.

Auto-discovery of accounts from folder names means dropping an existing archive
into `/archive` and running one scan is a complete import path — no manual
account creation, no CSV.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Settings, get_settings
from .db import transaction
from .hashing import sha256_file
from .logs import log_event
from .util import utc_now_iso

# Sidecar and junk files that live alongside real media.
IGNORED_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".directory"})
IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".download", ".json", ".txt", ".nfo")


@dataclass
class ScanReport:
    accounts_seen: int = 0
    accounts_created: int = 0
    files_seen: int = 0
    files_added: int = 0
    files_updated: int = 0
    files_rehashed: int = 0
    files_marked_missing: int = 0
    files_restored: int = 0
    duplicates_found: int = 0
    bytes_indexed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def classify(path: Path, settings: Settings) -> str | None:
    """Map a file to `image` / `video` / `other`, or None to ignore it.

    Classification is extension-based on purpose. Sniffing magic bytes would be
    more accurate but would mean opening every file on every scan, and a
    misfiled extension is a cosmetic problem here, not a correctness one.
    """
    name = path.name.lower()
    if name in IGNORED_NAMES or name.startswith("."):
        return None
    if name.endswith(IGNORED_SUFFIXES):
        return None
    ext = path.suffix.lower()
    if ext in settings.image_extensions:
        return "image"
    if ext in settings.video_extensions:
        return "video"
    return "other"


def _ensure_account(conn: sqlite3.Connection, name: str, report: ScanReport) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO accounts (name, display_name, status, platform_state, notes)
        VALUES (?, ?, 'active', 'unknown', 'Auto-created by archive scan')
        """,
        (name, name),
    )
    report.accounts_created += 1
    account_id = int(cursor.lastrowid)
    log_event(
        conn,
        level="info",
        source="scanner",
        event="account_discovered",
        message=f"Discovered account folder '{name}' during archive scan",
        account_id=account_id,
    )
    return account_id


def scan_account(
    conn: sqlite3.Connection,
    account_id: int,
    account_name: str,
    *,
    settings: Settings | None = None,
    archive_path: str | None = None,
    rehash: bool = False,
    report: ScanReport | None = None,
) -> ScanReport:
    """Reconcile one account folder with the index.

    `rehash=True` forces a full SHA-256 pass; otherwise a file whose size and
    mtime match the stored values is trusted and skipped.
    """
    settings = settings or get_settings()
    report = report or ScanReport()
    root = settings.account_dir(account_name, archive_path)
    now = utc_now_iso()

    if not root.is_dir():
        report.errors.append(f"{account_name}: folder missing ({root})")
        log_event(
            conn,
            level="warn",
            source="scanner",
            event="archive_folder_missing",
            message=f"Archive folder not found for '{account_name}'",
            account_id=account_id,
            detail={"expected_path": str(root)},
        )
        return report

    existing = {
        row["rel_path"]: row
        for row in conn.execute(
            """
            SELECT id, rel_path, bytes, mtime_ns, content_hash, deleted_at, is_missing, media_type
              FROM media_files WHERE account_id = ?
            """,
            (account_id,),
        )
    }
    seen_paths: set[str] = set()

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        media_type = classify(path, settings)
        if media_type is None:
            continue
        try:
            stat = path.stat()
        except OSError as exc:
            report.errors.append(f"{path}: {exc}")
            continue
        if stat.st_size < settings.min_media_bytes:
            continue

        rel_path = path.relative_to(root).as_posix()
        seen_paths.add(rel_path)
        report.files_seen += 1
        report.bytes_indexed += stat.st_size

        prior = existing.get(rel_path)
        unchanged = (
            prior is not None
            and prior["content_hash"] is not None
            and prior["bytes"] == stat.st_size
            and prior["mtime_ns"] == stat.st_mtime_ns
        )

        if unchanged and not rehash:
            if prior["is_missing"] or prior["deleted_at"]:
                # File came back (restored from a backup, or the user undeleted
                # it). Clear the tombstone so it counts again.
                conn.execute(
                    "UPDATE media_files SET is_missing = 0, deleted_at = NULL, last_verified_at = ? WHERE id = ?",
                    (now, prior["id"]),
                )
                report.files_restored += 1
            else:
                conn.execute(
                    "UPDATE media_files SET last_verified_at = ? WHERE id = ?",
                    (now, prior["id"]),
                )
            continue

        try:
            content_hash = sha256_file(path, settings.hash_chunk_bytes)
        except OSError as exc:
            report.errors.append(f"{path}: {exc}")
            continue
        report.files_rehashed += 1

        if prior is not None:
            conn.execute(
                """
                UPDATE media_files
                   SET media_type = ?, filename = ?, ext = ?, bytes = ?, mtime_ns = ?,
                       content_hash = ?, is_missing = 0, deleted_at = NULL,
                       imported_at = COALESCE(imported_at, ?), last_verified_at = ?
                 WHERE id = ?
                """,
                (
                    media_type,
                    path.name,
                    path.suffix.lower(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    content_hash,
                    now,
                    now,
                    prior["id"],
                ),
            )
            report.files_updated += 1
            continue

        try:
            conn.execute(
                """
                INSERT INTO media_files
                    (account_id, media_type, rel_path, filename, ext, bytes, mtime_ns,
                     content_hash, imported_at, first_seen_at, last_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    media_type,
                    rel_path,
                    path.name,
                    path.suffix.lower(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    content_hash,
                    now,
                    now,
                    now,
                ),
            )
            report.files_added += 1
        except sqlite3.IntegrityError:
            # The partial unique index on (account_id, content_hash) rejected
            # this: two files in the same folder with identical bytes. Index it
            # anyway with a NULL hash so the file stays visible in the UI, but
            # do not let it claim the dedup slot.
            conn.execute(
                """
                INSERT OR IGNORE INTO media_files
                    (account_id, media_type, rel_path, filename, ext, bytes, mtime_ns,
                     content_hash, imported_at, first_seen_at, last_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    account_id,
                    media_type,
                    rel_path,
                    path.name,
                    path.suffix.lower(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    now,
                    now,
                    now,
                ),
            )
            report.duplicates_found += 1

    vanished = [row for rel, row in existing.items() if rel not in seen_paths and not row["is_missing"]]
    for row in vanished:
        conn.execute("UPDATE media_files SET is_missing = 1 WHERE id = ?", (row["id"],))
    report.files_marked_missing += len(vanished)

    conn.execute("UPDATE accounts SET last_import_at = ? WHERE id = ?", (now, account_id))
    log_event(
        conn,
        level="info",
        source="scanner",
        event="scan_complete",
        message=(
            f"Indexed {report.files_seen} files "
            f"({report.files_added} new, {report.files_marked_missing} missing)"
        ),
        account_id=account_id,
        detail=report.as_dict(),
    )
    return report


def scan_archive(
    *,
    settings: Settings | None = None,
    conn: sqlite3.Connection,
    account_id: int | None = None,
    rehash: bool = False,
) -> ScanReport:
    """Scan one account, or every folder under `/archive` when `account_id` is None."""
    settings = settings or get_settings()
    report = ScanReport()
    settings.archive_root.mkdir(parents=True, exist_ok=True)

    with transaction(conn):
        if account_id is not None:
            row = conn.execute(
                "SELECT id, name, archive_path FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row is None:
                report.errors.append(f"account {account_id} not found")
                return report
            report.accounts_seen = 1
            scan_account(
                conn,
                int(row["id"]),
                row["name"],
                settings=settings,
                archive_path=row["archive_path"],
                rehash=rehash,
                report=report,
            )
            return report

        for folder in sorted(p for p in settings.archive_root.iterdir() if p.is_dir()):
            if folder.name.startswith("."):
                continue
            report.accounts_seen += 1
            resolved_id = _ensure_account(conn, folder.name, report)
            scan_account(
                conn,
                resolved_id,
                folder.name,
                settings=settings,
                archive_path=folder.name,
                rehash=rehash,
                report=report,
            )

    return report


def ensure_account_dirs(account_name: str, settings: Settings | None = None) -> Path:
    """Create `<archive>/<account>/{photos,videos}`. Idempotent."""
    settings = settings or get_settings()
    root = settings.account_dir(account_name)
    (root / settings.photos_dir).mkdir(parents=True, exist_ok=True)
    (root / settings.videos_dir).mkdir(parents=True, exist_ok=True)
    return root
