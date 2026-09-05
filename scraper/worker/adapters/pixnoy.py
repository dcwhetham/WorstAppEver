"""Pixnoy (formerly Picnob) profile adapter.

The first ~12 posts are only in the profile HTML (`.post_box`, `.cover_link`,
`.downbtn`). Pagination requires the encrypted `data-next` token from
`.more_btn`, posted to `/api/posts?userid=&next=`. Calling that API without
`next` is a no-op on current Pixnoy.

Cloudflare sits in front of the site. A challenge is treated as "this adapter
cannot run", so Imginn (or a later source) still gets a turn. Browser cookies
in `COOKIE_DIR/pixnoy.txt` are sent automatically when present.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from .base import AdapterError, ProfileInfo, RemoteItem
from .htmlparse import items_from_json_obj, parse_pixnoy_items, parse_pixnoy_profile
from .mirror import MirrorAdapter

_MAX_PAGES = 200


class PixnoyAdapter(MirrorAdapter):
    name = "pixnoy"
    rank = 30
    accepts = ("pixnoy",)
    default_origin = "https://www.pixnoy.com"

    def _profile_url(self, handle: str, links: list[dict[str, Any]]) -> str:
        return self.base_url(handle, links) or (f"{self.default_origin}/profile/{self.remote_handle(handle, links)}/")

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        url = self._profile_url(handle, links)
        html = self.fetch_page(url, headers=self.page_headers(url))
        self.classify_profile_page(html, handle)
        meta = parse_pixnoy_profile(html)
        items, _, _ = parse_pixnoy_items(html, base=url, provider=self.name)
        if not meta.get("display_name") and not meta.get("user_id") and not items:
            raise AdapterError(f"pixnoy profile page for '{handle}' did not look like a user profile")
        images = sum(1 for item in items if item.media_type == "image")
        videos = sum(1 for item in items if item.media_type == "video")
        return ProfileInfo(
            handle=handle,
            exists=True,
            media_total=meta.get("media_total"),
            image_total=images or None,
            video_total=videos or None,
            display_name=meta.get("display_name"),
        )

    def list_items(
        self,
        handle: str,
        links: list[dict[str, Any]],
        *,
        max_items: int | None = None,
        since: str | None = None,
    ) -> Iterator[RemoteItem]:
        url = self._profile_url(handle, links)
        html = self.fetch_page(url, headers=self.page_headers(url))
        self.classify_profile_page(html, handle)
        items, cursor, _ = parse_pixnoy_items(html, base=url, provider=self.name)
        meta = parse_pixnoy_profile(html)
        user_id = meta.get("user_id")

        yielded = 0
        seen: set[str] = set()
        pages = 0

        while True:
            pages += 1
            if pages > _MAX_PAGES:
                return
            for item in items:
                if item.remote_id in seen:
                    continue
                if since and item.posted_at and item.posted_at < since:
                    continue
                seen.add(item.remote_id)
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            if not cursor:
                return
            items, cursor = self._next_page(url, user_id=user_id, cursor=cursor)

    def _next_page(
        self,
        profile_url: str,
        *,
        user_id: str | None,
        cursor: str,
    ) -> tuple[list[RemoteItem], str | None]:
        origin = self.origin_of(profile_url)
        params = {"userid": user_id or "", "next": cursor}
        endpoints = (
            urljoin(origin + "/", "api/posts"),
            urljoin(origin + "/", "api/posts/"),
        )
        last_error: Exception | None = None
        for endpoint in endpoints:
            for method in ("GET", "POST"):
                try:
                    kwargs: dict[str, Any] = {"headers": self.page_headers(profile_url, ajax=True)}
                    if method == "GET":
                        kwargs["params"] = params
                    else:
                        kwargs["data"] = params
                    body = self.fetch_page(endpoint, method=method, **kwargs)
                except AdapterError as exc:
                    last_error = exc
                    continue
                items, next_cursor = _parse_pixnoy_page_body(body, base=profile_url)
                if items or next_cursor is not None:
                    return items, next_cursor
        if last_error is not None:
            raise AdapterError(f"pixnoy load-more failed after cursor {cursor!r}: {last_error}") from last_error
        return [], None


def _parse_pixnoy_page_body(body: str, *, base: str) -> tuple[list[RemoteItem], str | None]:
    stripped = body.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body
        return items_from_json_obj(payload, base=base, provider="pixnoy")
    items, cursor, _ = parse_pixnoy_items(body, base=base, provider="pixnoy")
    return items, cursor
