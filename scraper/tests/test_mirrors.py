"""Imginn and Pixnoy adapters, tested against saved HTML/JSON — no live network."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path

from conftest import make_account, queue_job

from worker.adapters.base import (
    AccountPrivateError,
    AccountUnavailableError,
    AdapterError,
    AdapterUnavailableError,
)
from worker.adapters.htmlparse import (
    items_from_json_obj,
    looks_like_challenge,
    parse_imginn_items,
    parse_imginn_profile,
    parse_pixnoy_items,
    parse_pixnoy_profile,
)
from worker.adapters.imginn import ImginnAdapter
from worker.adapters.mirror import InstagramAdapter
from worker.adapters.pixnoy import PixnoyAdapter
from worker.sync import SyncEngine

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


class ScriptedClient:
    """Stand-in for HttpClient. Routes are (method or None, url substring, body|bytes|exc)."""

    def __init__(self, routes: list[tuple[str | None, str, object]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: object) -> str:
        self.calls.append((method.upper(), url))
        for meth, needle, payload in self.routes:
            if meth and meth.upper() != method.upper():
                continue
            if needle not in url:
                continue
            if isinstance(payload, Exception):
                raise payload
            if isinstance(payload, bytes):
                return payload.decode()
            return str(payload)
        raise AdapterError(f"unexpected {method} {url}")

    def get_text(self, url: str, **kwargs: object) -> str:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: object) -> object:
        return json.loads(self.request("GET", url, **kwargs))

    @contextmanager
    def stream(self, url: str, **kwargs: object):
        body = self.request("GET", url, **kwargs)
        raw = body.encode() if isinstance(body, str) else body
        yield io.BytesIO(raw)


def _imginn() -> ImginnAdapter:
    adapter = ImginnAdapter(rotate_user_agent=False)
    return adapter


def _pixnoy() -> PixnoyAdapter:
    return PixnoyAdapter(rotate_user_agent=False)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_imginn_parser_reads_profile_items_and_skips_templates():
    html = _read("imginn/profile.html")
    meta = parse_imginn_profile(html)
    items, cursor, user_id = parse_imginn_items(html, base="https://imginn.com/demo.user/")

    assert meta["display_name"] == "Demo User"
    assert user_id == "4242"
    assert cursor == "CUR_PAGE_2"
    assert [item.remote_id for item in items] == ["AaBbCc11111", "VvWwXx22222"]
    assert items[0].media_type == "image"
    assert items[1].media_type == "video"
    assert "one.jpg" in items[0].url
    assert "clip.mp4" in items[1].url
    assert "dl=" not in items[1].url
    assert items[0].url.startswith("https://scontent.cdninstagram.com/")


def test_pixnoy_parser_reads_counts_items_and_next_token():
    html = _read("pixnoy/profile.html")
    meta = parse_pixnoy_profile(html)
    items, cursor, _ = parse_pixnoy_items(html, base="https://www.pixnoy.com/profile/demo.user/")

    assert meta["display_name"] == "Demo User"
    assert meta["user_id"] == "4242"
    assert meta["media_total"] == 42
    assert cursor == "NEXT_TOKEN_2"
    assert [item.remote_id for item in items] == ["AaBbCc11111", "VvWwXx22222"]
    assert items[1].media_type == "video"
    assert "dl=" not in items[0].url


def test_pixnoy_json_page_parses_structured_items():
    payload = json.loads(_read("pixnoy/page2.json"))
    items, cursor = items_from_json_obj(payload, base="https://www.pixnoy.com/", provider="pixnoy")
    assert [item.remote_id for item in items] == ["YyZzAa33333"]
    assert items[0].media_type == "image"
    assert cursor is None


def test_challenge_page_is_detected():
    assert looks_like_challenge(_read("imginn/challenge.html"))
    assert looks_like_challenge(_read("pixnoy/challenge.html"))


# ---------------------------------------------------------------------------
# Adapters + scripted HTTP
# ---------------------------------------------------------------------------


def test_imginn_probe_and_paginated_list():
    adapter = _imginn()
    adapter.client = ScriptedClient(
        [
            (None, "/demo.user/", _read("imginn/profile.html")),
            (None, "api/posts", _read("imginn/page2.html")),
        ]
    )
    links = [{"provider": "imginn", "url": "https://imginn.com/demo.user/", "sort_order": 20}]

    profile = adapter.probe("demo.user", links)
    assert profile.exists
    assert profile.display_name == "Demo User"
    assert profile.image_total == 1
    assert profile.video_total == 1

    items = list(adapter.list_items("demo.user", links))
    assert [item.remote_id for item in items] == ["AaBbCc11111", "VvWwXx22222", "YyZzAa33333"]
    assert any("api/posts" in url for _, url in adapter.client.calls)


def test_imginn_private_and_missing():
    adapter = _imginn()
    adapter.client = ScriptedClient([(None, "/hidden.user/", _read("imginn/private.html"))])
    links = [{"provider": "imginn", "url": "https://imginn.com/hidden.user/", "sort_order": 20}]
    try:
        adapter.probe("hidden.user", links)
        raise AssertionError("expected AccountPrivateError")
    except AccountPrivateError:
        pass

    adapter.client = ScriptedClient([(None, "/gone.user/", _read("imginn/missing.html"))])
    links = [{"provider": "imginn", "url": "https://imginn.com/gone.user/", "sort_order": 20}]
    try:
        adapter.probe("gone.user", links)
        raise AssertionError("expected AccountUnavailableError")
    except AccountUnavailableError:
        pass


def test_imginn_challenge_is_adapter_unavailable_not_account_failure():
    adapter = _imginn()
    adapter.client = ScriptedClient([(None, "/demo.user/", _read("imginn/challenge.html"))])
    links = [{"provider": "imginn", "url": "https://imginn.com/demo.user/", "sort_order": 20}]
    try:
        adapter.probe("demo.user", links)
        raise AssertionError("expected AdapterUnavailableError")
    except AdapterUnavailableError as exc:
        assert "Cloudflare" in str(exc) or "challenge" in str(exc).lower()


def test_pixnoy_probe_and_json_pagination():
    adapter = _pixnoy()
    adapter.client = ScriptedClient(
        [
            (None, "/profile/demo.user/", _read("pixnoy/profile.html")),
            (None, "api/posts", _read("pixnoy/page2.json")),
        ]
    )
    links = [{"provider": "pixnoy", "url": "https://www.pixnoy.com/profile/demo.user/", "sort_order": 30}]

    profile = adapter.probe("demo.user", links)
    assert profile.display_name == "Demo User"
    assert profile.media_total == 42

    items = list(adapter.list_items("demo.user", links))
    assert [item.remote_id for item in items] == ["AaBbCc11111", "VvWwXx22222", "YyZzAa33333"]


def test_pixnoy_private_and_challenge():
    adapter = _pixnoy()
    adapter.client = ScriptedClient([(None, "/profile/hidden.user/", _read("pixnoy/private.html"))])
    links = [{"provider": "pixnoy", "url": "https://www.pixnoy.com/profile/hidden.user/", "sort_order": 30}]
    try:
        adapter.probe("hidden.user", links)
        raise AssertionError("expected AccountPrivateError")
    except AccountPrivateError:
        pass

    adapter.client = ScriptedClient([(None, "/profile/demo.user/", _read("pixnoy/challenge.html"))])
    links = [{"provider": "pixnoy", "url": "https://www.pixnoy.com/profile/demo.user/", "sort_order": 30}]
    try:
        adapter.probe("demo.user", links)
        raise AssertionError("expected AdapterUnavailableError")
    except AdapterUnavailableError:
        pass


def test_instagram_is_still_a_stub():
    adapter = InstagramAdapter(rotate_user_agent=False)
    try:
        links = [{"provider": "instagram", "url": "https://www.instagram.com/anyone/", "sort_order": 10}]
        adapter.probe("anyone", links)
        raise AssertionError("expected AdapterUnavailableError")
    except AdapterUnavailableError as exc:
        assert "stub" in str(exc)


# ---------------------------------------------------------------------------
# Engine: a scripted Imginn adapter actually archives files
# ---------------------------------------------------------------------------


def test_sync_engine_downloads_via_imginn(conn, env, settings):
    account_id = make_account(conn, "demo.user", last_success="2026-01-01T00:00:00Z")
    adapter = _imginn()
    media = {
        "https://scontent.cdninstagram.com/v/t51.2885-15/one.jpg?stp=dst-jpg_e35&oh=abc": b"imginn-photo" * 20,
        "https://scontent.cdninstagram.com/v/t66.30100-16/clip.mp4?oh=def": b"imginn-video" * 20,
        "https://scontent.cdninstagram.com/v/t51.2885-15/three.jpg": b"imginn-older" * 20,
    }

    class DownloadingClient(ScriptedClient):
        def request(self, method: str, url: str, **kwargs: object) -> str:
            if url in media:
                self.calls.append((method.upper(), url))
                return media[url].decode("latin1")
            return super().request(method, url, **kwargs)

        @contextmanager
        def stream(self, url: str, **kwargs: object):
            if url not in media:
                raise AdapterError(f"no media for {url}")
            yield io.BytesIO(media[url])

    adapter.client = DownloadingClient(
        [
            (None, "/demo.user/", _read("imginn/profile.html")),
            (None, "api/posts", _read("imginn/page2.html")),
        ]
    )

    result = SyncEngine(conn, env, settings, [adapter]).run(queue_job(conn, account_id))
    assert result.status == "succeeded"
    assert result.adapter_used == "imginn"
    assert result.downloaded == 3
    assert len(list((env.archive_root / "demo.user" / "photos").glob("*.jpg"))) == 2
    assert len(list((env.archive_root / "demo.user" / "videos").glob("*.mp4"))) == 1


def test_fallback_skips_challenged_imginn_and_uses_pixnoy(conn, env, settings):
    account_id = make_account(conn, "demo.user", last_success="2026-01-01T00:00:00Z")
    conn.execute(
        """
        INSERT OR IGNORE INTO account_links (account_id, provider, kind, url, sort_order)
        VALUES (?, 'pixnoy', 'derived', ?, 30)
        """,
        (account_id, "https://www.pixnoy.com/profile/demo.user/"),
    )

    imginn = _imginn()
    imginn.client = ScriptedClient([(None, "imginn.com", _read("imginn/challenge.html"))])

    pixnoy = _pixnoy()
    photo = b"pixnoy-photo-bytes" * 16

    class PixClient(ScriptedClient):
        @contextmanager
        def stream(self, url: str, **kwargs: object):
            yield io.BytesIO(photo)

    pixnoy.client = PixClient(
        [
            (None, "/profile/demo.user/", _read("pixnoy/profile.html")),
            (None, "api/posts", _read("pixnoy/page2.json")),
        ]
    )

    result = SyncEngine(conn, env, settings, [imginn, pixnoy]).run(queue_job(conn, account_id))
    assert result.status == "succeeded"
    assert result.adapter_used == "pixnoy"
    assert result.downloaded >= 1
