"""Content hashing and deduplication primitives.

Deduplication happens at three widening levels of cost, and the downloader walks
them in order so the expensive one runs least often:

1. `(provider, remote_id)` — free, a lookup in `remote_index`.
2. Quick signature (size + head/tail sample) — two seeks, catches re-encodes of
   the same file under a new remote id.
3. Full SHA-256 — reads the whole file, and is the only thing trusted to write
   `media_files.content_hash`.

Level 2 is not a substitute for level 3. It is a filter that lets us skip the
full read for the overwhelming majority of non-matches.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHUNK = 1024 * 1024
# Enough to capture container headers and trailing indexes for common formats.
SAMPLE_BYTES = 64 * 1024


def sha256_file(path: Path | str, chunk_size: int = DEFAULT_CHUNK) -> str:
    """Streaming SHA-256 so a 4 GB video does not become a 4 GB allocation."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def quick_signature(path: Path | str, sample_bytes: int = SAMPLE_BYTES) -> str:
    """Cheap fingerprint from file size plus the first and last `sample_bytes`.

    Two files with different quick signatures cannot be identical, so a
    mismatch is a definitive skip. A match is only a hint — always confirm with
    `sha256_file` before treating it as a duplicate.
    """
    path = Path(path)
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with open(path, "rb") as handle:
        digest.update(handle.read(sample_bytes))
        if size > sample_bytes * 2:
            handle.seek(-sample_bytes, 2)
            digest.update(handle.read(sample_bytes))
    return f"qs1:{size}:{digest.hexdigest()[:32]}"


@dataclass(frozen=True)
class DuplicateMatch:
    media_file_id: int
    account_id: int
    rel_path: str
    same_account: bool


def find_duplicate(
    conn: sqlite3.Connection,
    content_hash: str,
    account_id: int | None = None,
) -> DuplicateMatch | None:
    """Locate an existing live file with these exact bytes.

    Matches inside the requesting account are returned first: a cross-account
    hit is informational (the same photo reposted by a mirror) and should not by
    itself stop a download, whereas a same-account hit means we already have it.
    """
    rows = conn.execute(
        """
        SELECT id, account_id, rel_path
          FROM media_files
         WHERE content_hash = ?
           AND deleted_at IS NULL
         ORDER BY (account_id = ?) DESC, id
         LIMIT 1
        """,
        (content_hash, account_id or -1),
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return DuplicateMatch(
        media_file_id=row["id"],
        account_id=row["account_id"],
        rel_path=row["rel_path"],
        same_account=account_id is not None and row["account_id"] == account_id,
    )


def known_hashes(conn: sqlite3.Connection, account_id: int) -> set[str]:
    """All live content hashes for one account.

    Loading the set once per run beats a query per candidate: a 5k-file account
    is well under a megabyte of hex in memory, and the sync loop then does its
    dedup checks without touching the database.
    """
    rows = conn.execute(
        """
        SELECT content_hash FROM media_files
         WHERE account_id = ? AND content_hash IS NOT NULL AND deleted_at IS NULL
        """,
        (account_id,),
    ).fetchall()
    return {row[0] for row in rows}
