"""Offline adapter that serves items from a local directory.

Exists so the whole pipeline — discovery, pacing, hashing, dedup, atomic writes,
progress reporting, ETA — can be exercised end to end with no network and no
target site. That matters more than it sounds: the interesting bugs in this
system are in the incremental and dedup logic, and those are exactly the parts
you cannot test reliably against a live source.

Enable by pointing `SCRAPER_FIXTURE_DIR` at a folder laid out like an archive:

    fixtures/
      aurora.films/
        photos/one.jpg
        videos/clip.mp4

Runs first in the chain when configured, and is inert otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from .base import AdapterUnavailableError, ProfileInfo, RemoteItem

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def fixture_root() -> Path | None:
    raw = os.getenv("SCRAPER_FIXTURE_DIR")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


class FixtureAdapter:
    name = "fixture"
    rank = 0

    def supports(self, handle: str, links: list[dict[str, Any]]) -> bool:
        root = fixture_root()
        return root is not None and (root / handle).is_dir()

    def _files(self, handle: str) -> list[Path]:
        root = fixture_root()
        if root is None:
            raise AdapterUnavailableError("SCRAPER_FIXTURE_DIR is not set")
        folder = root / handle
        if not folder.is_dir():
            raise AdapterUnavailableError(f"no fixture folder for '{handle}'")
        return sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("."))

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        files = self._files(handle)
        images = sum(1 for p in files if p.suffix.lower() in IMAGE_EXT)
        videos = sum(1 for p in files if p.suffix.lower() in VIDEO_EXT)
        return ProfileInfo(
            handle=handle,
            exists=True,
            media_total=len(files),
            image_total=images,
            video_total=videos,
            display_name=handle,
        )

    def list_items(
        self,
        handle: str,
        links: list[dict[str, Any]],
        *,
        max_items: int | None = None,
        since: str | None = None,
    ) -> Iterator[RemoteItem]:
        root = fixture_root()
        assert root is not None
        files = self._files(handle)
        # Newest-first, matching the ordering real adapters must provide so the
        # engine's early-exit heuristic behaves identically here.
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for index, path in enumerate(files):
            if max_items is not None and index >= max_items:
                return
            suffix = path.suffix.lower()
            media_type = "image" if suffix in IMAGE_EXT else "video" if suffix in VIDEO_EXT else "other"
            rel = path.relative_to(root / handle).as_posix()
            yield RemoteItem(
                # Path-derived and stable, which is what a real remote_id must be.
                remote_id=f"fx-{rel.replace('/', '-')}",
                # `url` is the only download handle that survives the round-trip
                # through `remote_index`, so the fixture uses it exactly as a real
                # adapter must. Stashing the path in `extra` would work in-process
                # and then fail on the resumed run — which is the bug this
                # deliberately avoids reintroducing.
                url=path.as_uri(),
                media_type=media_type,
                provider=self.name,
                size_bytes=path.stat().st_size,
                filename_hint=path.stem,
            )

    @contextmanager
    def open_stream(self, item: RemoteItem) -> Iterator[IO[bytes]]:
        if not item.url.startswith("file://"):
            raise AdapterUnavailableError(f"fixture item {item.remote_id} has no file:// url")
        source = Path(url2pathname(urlparse(item.url).path))
        if not source.is_file():
            raise AdapterUnavailableError(f"fixture source vanished: {source}")
        with open(source, "rb") as handle:
            yield handle
