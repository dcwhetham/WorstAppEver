"""Imginn profile adapter.

Imginn is a public HTML viewer. The first page of posts is in the profile
document (`div.item`, `a.download`, `button.load-more`). Further pages come
from `/api/posts` with the opaque `data-cursor` the first page handed us.

Nothing here bypasses Cloudflare. If the datacenter IP is challenged, export
browser cookies into `COOKIE_DIR/imginn.txt` and the existing HttpClient will
send them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from .base import AdapterError, ProfileInfo, RemoteItem
from .htmlparse import items_from_json_obj, parse_imginn_items, parse_imginn_profile
from .mirror import MirrorAdapter

# Hard cap so a broken cursor cannot spin the worker forever.
_MAX_PAGES = 200


class ImginnAdapter(MirrorAdapter):
    name = "imginn"
    rank = 20
    accepts = ("imginn",)
    default_origin = "https://imginn.com"

    def _profile_url(self, handle: str, links: list[dict[str, Any]]) -> str:
        return self.base_url(handle, links) or f"{self.default_origin}/{self.remote_handle(handle, links)}/"

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        url = self._profile_url(handle, links)
        html = self.fetch_page(url, headers=self.page_headers(url))
        self.classify_profile_page(html, handle)
        meta = parse_imginn_profile(html)
        items, _, _ = parse_imginn_items(html, base=url, provider=self.name)
        if not meta.get("display_name") and not meta.get("user_id") and not items:
            raise AdapterError(f"imginn profile page for '{handle}' did not look like a user profile")
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
        items, cursor, user_id = parse_imginn_items(html, base=url, provider=self.name)
        meta = parse_imginn_profile(html)
        user_id = user_id or meta.get("user_id")
        username = self.remote_handle(handle, links)

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
            items, cursor = self._next_page(url, user_id=user_id, username=username, cursor=cursor)

    def _next_page(
        self,
        profile_url: str,
        *,
        user_id: str | None,
        username: str,
        cursor: str,
    ) -> tuple[list[RemoteItem], str | None]:
        origin = self.origin_of(profile_url)
        params = {"id": user_id or "", "cursor": cursor, "username": username, "type": "user"}
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
                items, next_cursor = _parse_imginn_page_body(body, base=profile_url)
                if items or next_cursor is not None:
                    return items, next_cursor
        if last_error is not None:
            raise AdapterError(f"imginn load-more failed after cursor {cursor!r}: {last_error}") from last_error
        return [], None


def _parse_imginn_page_body(body: str, *, base: str) -> tuple[list[RemoteItem], str | None]:
    stripped = body.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body
        return items_from_json_obj(payload, base=base, provider="imginn")
    items, cursor, _ = parse_imginn_items(body, base=base, provider="imginn")
    return items, cursor
