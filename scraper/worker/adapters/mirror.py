"""Mirror-site adapters (Imginn, Pixnoy, and friends).

**These are deliberately unimplemented.** Every method raises
`AdapterUnavailableError`, which the sync engine treats as "skip to the next
adapter" — so the scaffold runs, the fallback chain is exercised, and nothing
silently pretends to work.

The reason they are stubs is not laziness. Mirror sites are scraped by parsing
undocumented HTML that changes without notice, and their terms and legality vary
by site and jurisdiction. A shipped parser would be broken within weeks and would
make a legal decision on the operator's behalf. What the scaffold provides
instead is the *shape*: fill in three methods and the incremental logic, pacing,
deduplication, retries, logging and progress reporting all work unchanged.

To implement one:

1. `probe()` — fetch the profile page, return `ProfileInfo`. Raise
   `AccountUnavailableError` on 404 and `AccountPrivateError` on a private
   profile. `self.client` already maps status codes to those exceptions.
2. `list_items()` — yield `RemoteItem` **newest-first**, one page at a time, as a
   generator. Do not pre-collect: the engine stops consuming after
   `KNOWN_STREAK_LIMIT` known items, and that early exit is what keeps a routine
   sync down to one or two requests.
3. `open_stream()` — `with self.client.stream(item.url) as body: yield body`.

Two things to get right:

* **`remote_id` must be stable across runs.** Derive it from the post shortcode
  or the CDN path, never from a signed URL whose query string rotates — every run
  would otherwise look like an account full of new files.
* **Set `content_hash`/`size_bytes` when the response headers offer them.** They
  let the engine skip a download before spending any bandwidth on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from ..net import HttpClient
from .base import AdapterUnavailableError, ProfileInfo, RemoteItem


class MirrorAdapter:
    """Shared scaffolding for mirror-site adapters."""

    name = "mirror"
    rank = 50
    #: Providers in `account_links` this adapter can consume.
    accepts: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        proxies: list[str] | None = None,
        cookie_dir: Path | None = None,
        timeout: float = 45.0,
        rotate_user_agent: bool = True,
    ) -> None:
        self.client = HttpClient(
            provider=self.name,
            timeout=timeout,
            proxies=proxies,
            cookie_dir=cookie_dir,
            rotate_user_agent=rotate_user_agent,
        )

    def supports(self, handle: str, links: list[dict[str, Any]]) -> bool:
        return any(link["provider"] in self.accepts for link in links)

    def base_url(self, handle: str, links: list[dict[str, Any]]) -> str | None:
        """First matching link, honouring `remote_handle` for renamed mirrors."""
        for link in sorted(links, key=lambda item: item.get("sort_order", 100)):
            if link["provider"] in self.accepts:
                return link["url"]
        return None

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        raise AdapterUnavailableError(f"{self.name} adapter is a stub: implement probe() to enable this source")

    def list_items(
        self,
        handle: str,
        links: list[dict[str, Any]],
        *,
        max_items: int | None = None,
        since: str | None = None,
    ) -> Iterator[RemoteItem]:
        raise AdapterUnavailableError(f"{self.name} adapter is a stub: implement list_items() to enable this source")
        yield  # pragma: no cover - keeps this a generator for type checkers

    @contextmanager
    def open_stream(self, item: RemoteItem) -> Iterator[IO[bytes]]:
        """Default implementation: most mirrors serve media as a plain GET.

        Subclasses only need to override this when the CDN requires a signed URL
        refresh or a Referer header.
        """
        with self.client.stream(item.url) as body:
            yield body


class ImginnAdapter(MirrorAdapter):
    name = "imginn"
    rank = 20
    accepts = ("imginn",)


class PixnoyAdapter(MirrorAdapter):
    name = "pixnoy"
    rank = 30
    accepts = ("pixnoy",)


class InstagramAdapter(MirrorAdapter):
    """Direct source.

    Ranked last despite being the canonical source: it is the one that requires
    authentication, and it is the one where aggressive access has consequences for
    the account. Mirrors are tried first precisely because failing against a
    mirror costs nothing.
    """

    name = "instagram"
    rank = 90
    accepts = ("instagram",)

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        raise AdapterUnavailableError(
            "instagram adapter is a stub: implement probe() and supply session cookies "
            "in COOKIE_DIR/instagram.txt to enable it"
        )
