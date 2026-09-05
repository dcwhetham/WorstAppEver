"""Shared scaffolding for mirror-site adapters, plus the Instagram stub.

Imginn and Pixnoy live in their own modules: they parse undocumented HTML that
changes without notice, and isolating that code keeps a markup break from
touching the rest of the worker.

Instagram stays a stub on purpose. The official Graph API is the only path that
does not involve session cookies or stealth tricks, and it is not wired up here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

from ..net import HttpClient
from .base import (
    AccountPrivateError,
    AccountUnavailableError,
    AdapterError,
    AdapterUnavailableError,
    BlockedError,
    ProfileInfo,
    RemoteItem,
)
from .htmlparse import (
    extract_media_from_post_page,
    is_direct_media,
    looks_like_challenge,
    looks_like_missing,
    looks_like_private,
)

COOKIE_HINT = (
    "Export a Netscape cookies.txt from a real browser session (including "
    "cf_clearance if Cloudflare challenged you) into COOKIE_DIR/{provider}.txt"
)


class MirrorAdapter:
    """Shared scaffolding for mirror-site adapters."""

    name = "mirror"
    rank = 50
    #: Providers in `account_links` this adapter can consume.
    accepts: tuple[str, ...] = ()
    default_origin: str = ""

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

    def remote_handle(self, handle: str, links: list[dict[str, Any]]) -> str:
        for link in sorted(links, key=lambda item: item.get("sort_order", 100)):
            if link["provider"] in self.accepts and link.get("remote_handle"):
                return str(link["remote_handle"])
        return handle

    def origin_of(self, url: str | None) -> str:
        if url:
            parts = urlsplit(url)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}"
        return self.default_origin

    def page_headers(self, page_url: str, *, ajax: bool = False) -> dict[str, str]:
        headers = {"Referer": page_url}
        if ajax:
            headers.update(
                {
                    "Accept": "application/json, text/html, */*;q=0.8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                }
            )
        return headers

    def fetch_page(self, url: str, *, headers: dict[str, str] | None = None, method: str = "GET", **kwargs: Any) -> str:
        """GET/POST a document, mapping transport walls onto the fallback chain.

        A 404 or Cloudflare challenge on *this* mirror says nothing about the
        account — the next adapter may still work — so those become
        `AdapterUnavailableError`. Private/missing copy in a 200 body is the
        account-level signal and is left for the caller to raise.
        """
        try:
            body = self.client.request(method, url, headers=headers, **kwargs)
        except AccountUnavailableError as exc:
            raise AdapterUnavailableError(f"{self.name} returned 404 for {url}") from exc
        except BlockedError as exc:
            raise AdapterUnavailableError(
                f"{self.name} blocked by bot detection at {url}. {COOKIE_HINT.format(provider=self.name)}"
            ) from exc
        except AdapterError:
            raise
        if looks_like_challenge(body):
            raise AdapterUnavailableError(
                f"{self.name} returned a Cloudflare challenge for {url}. {COOKIE_HINT.format(provider=self.name)}"
            )
        return body

    def classify_profile_page(self, html: str, handle: str) -> None:
        if looks_like_private(html):
            raise AccountPrivateError(f"'{handle}' is private via {self.name}")
        if looks_like_missing(html):
            raise AccountUnavailableError(f"'{handle}' not found via {self.name}")

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

    def post_url_for(self, item: RemoteItem) -> str | None:
        origin = self.origin_of(item.url) or self.default_origin
        if not origin or not item.remote_id:
            return None
        if self.name == "imginn":
            return f"{origin.rstrip('/')}/p/{item.remote_id}/"
        if self.name == "pixnoy":
            return f"{origin.rstrip('/')}/post/{item.remote_id}/"
        return None

    def resolve_media_url(self, item: RemoteItem) -> str:
        if is_direct_media(item.url):
            return item.url
        post_url = item.url if "/p/" in item.url or "/post/" in item.url else self.post_url_for(item)
        if not post_url:
            return item.url
        html = self.fetch_page(post_url, headers=self.page_headers(post_url))
        found = extract_media_from_post_page(html, base=post_url)
        if not found:
            raise AdapterError(f"{self.name} post page {post_url} had no downloadable media")
        return found

    @contextmanager
    def open_stream(self, item: RemoteItem) -> Iterator[IO[bytes]]:
        """GET the media, refreshing via the post page when a CDN URL has died."""
        referer = self.origin_of(item.url) or self.default_origin or item.url
        headers = {"Referer": referer + ("/" if referer and not referer.endswith("/") else "")}
        url = item.url
        if not is_direct_media(url):
            url = self.resolve_media_url(item)
        try:
            with self.client.stream(url, headers=headers) as body:
                yield body
            return
        except (AccountUnavailableError, BlockedError, AdapterError):
            if url != item.url and not is_direct_media(item.url):
                raise
            refreshed = self.resolve_media_url(item)
            if refreshed == url:
                raise
        with self.client.stream(refreshed, headers=headers) as body:
            yield body


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
    default_origin = "https://www.instagram.com"

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        raise AdapterUnavailableError(
            "instagram adapter is a stub: implement probe() and supply session cookies "
            "in COOKIE_DIR/instagram.txt to enable it"
        )
