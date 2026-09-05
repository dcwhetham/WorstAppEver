"""Hashing helpers for the download path.

The important one is `HashingWriter`: it hashes and writes in the same pass, so a
downloaded file is never read twice and never fully buffered in memory. That is
what lets deduplication be exact (full SHA-256, not a size heuristic) without
paying for it in I/O.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import IO

CHUNK = 1024 * 1024


def sha256_file(path: Path, chunk_size: int = CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


class HashingWriter:
    """Copy a stream to disk while computing its SHA-256.

    Usage:

        with HashingWriter(tmp_path) as writer:
            writer.copy_from(stream)
        writer.hexdigest, writer.bytes_written
    """

    def __init__(self, target: Path, chunk_size: int = CHUNK) -> None:
        self.target = target
        self.chunk_size = chunk_size
        self._digest = hashlib.sha256()
        self._handle: IO[bytes] | None = None
        self.bytes_written = 0

    def __enter__(self) -> HashingWriter:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.target, "wb")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def copy_from(self, stream: IO[bytes], max_bytes: int | None = None) -> int:
        """Stream into the target file, hashing as we go.

        `max_bytes` guards against a source that lies about Content-Length or
        streams forever; exceeding it raises rather than filling the disk.
        """
        assert self._handle is not None, "use HashingWriter as a context manager"
        while block := stream.read(self.chunk_size):
            self._digest.update(block)
            self._handle.write(block)
            self.bytes_written += len(block)
            if max_bytes is not None and self.bytes_written > max_bytes:
                raise ValueError(f"stream exceeded {max_bytes} byte limit")
        return self.bytes_written

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def known_hashes(conn: sqlite3.Connection, account_id: int) -> set[str]:
    """Every live content hash for the account, loaded once per run.

    One query up front beats a query per candidate item, and a 5,000-file account
    is well under a megabyte of hex in memory.
    """
    return {
        row[0]
        for row in conn.execute(
            """
            SELECT content_hash FROM media_files
             WHERE account_id = ? AND content_hash IS NOT NULL AND deleted_at IS NULL
            """,
            (account_id,),
        )
    }


def hash_exists_anywhere(conn: sqlite3.Connection, content_hash: str) -> sqlite3.Row | None:
    """Cross-account lookup: do these exact bytes already exist somewhere?

    Used for reporting, not for skipping. Two accounts legitimately holding the
    same reposted image should each keep their own copy; the archive is organised
    by account, and a hardlink farm would make folders non-portable.
    """
    return conn.execute(
        """
        SELECT id, account_id, rel_path FROM media_files
         WHERE content_hash = ? AND deleted_at IS NULL
         LIMIT 1
        """,
        (content_hash,),
    ).fetchone()
