"""Defensive HTML/JSON helpers for mirror-site adapters.

The sites this scraper talks to change their markup without notice. Everything
here is written to fail loudly on an unrecognised page (Cloudflare interstitial,
empty parse) rather than invent remote ids from noise. Parsers prefer a
download href over a thumbnail, and they never use a CDN query string as an id.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .base import RemoteItem

# Tokens that mean the response is a bot wall, not a profile.
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-mitigated",
    "challenge-platform",
    "checking your browser",
    "cdn-cgi/challenge",
    "attention required! | cloudflare",
)

_PRIVATE_MARKERS = (
    "this account is private",
    "this profile is private",
    "account is private",
    "this user is private",
)

_MISSING_MARKERS = (
    "sorry, this page isn't available",
    "sorry, this page isn&#39;t available",
    "user not found",
    "page not found",
    "the link you followed may be broken",
    "this page is not available",
)

# Placeholders from doT / mustache templates that sit in the same HTML as real posts.
_TEMPLATE_TOKEN = re.compile(r"[{}]|value\.|it\.")

_ATTR = re.compile(
    r"""(?:data-)?(?P<key>href|src|data-src|data-cursor|data-id|data-username|"""
    r"""data-type|data-next|data-maxid|data-name)\s*=\s*(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.I | re.S,
)

_COUNT_NUM = re.compile(r"[\d,]+")

_RELATIVE_DATE = re.compile(
    r"(?P<n>\d+)\s*(?P<unit>second|minute|hour|day|week|month|year)s?\s*ago",
    re.I,
)

_IMGINN_POST = re.compile(r"/p/([A-Za-z0-9_-]{5,})/?")
_PIXNOY_POST = re.compile(r"/post/([A-Za-z0-9_-]{5,})/?")

# A URL we can hand to `open_stream` without first fetching a post page.
_MEDIA_HINT = re.compile(
    r"\.(?:jpe?g|png|webp|gif|avif|mp4|mov|m4v|webm)(?:$|[?#])",
    re.I,
)
_MEDIA_HOST = re.compile(
    r"(cdninstagram\.com|fbcdn\.net|imginn\.com|pixnoy\.com|picnob\.com|cdn\d+\.)",
    re.I,
)

LAZY_PLACEHOLDERS = ("lazy.jpg", "lazy.png", "placeholder", "data:image")


def looks_like_challenge(html: str) -> bool:
    lowered = html[:8000].lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def looks_like_private(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _PRIVATE_MARKERS)


def looks_like_missing(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _MISSING_MARKERS)


def clean_url(value: str | None, base: str | None = None) -> str | None:
    """Unescape, absolutise, and drop the `dl=1` flag some mirrors add."""
    if not value:
        return None
    url = unescape(value).strip()
    if not url or url.startswith(("javascript:", "#")):
        return None
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.query:
        kept = [pair for pair in parts.query.split("&") if pair and pair.split("=", 1)[0].lower() != "dl"]
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))
    return url or None


def is_direct_media(url: str) -> bool:
    if not url:
        return False
    if _MEDIA_HINT.search(url):
        return True
    hosted = bool(_MEDIA_HOST.search(url))
    return hosted and "/p/" not in url and "/post/" not in url and "/profile/" not in url


def is_template_noise(value: str) -> bool:
    return bool(_TEMPLATE_TOKEN.search(value))


def first_attr(html: str, *names: str) -> str | None:
    found = {m.group("key").lower(): m.group("val") for m in _ATTR.finditer(html)}
    for name in names:
        value = found.get(name.lower())
        if value:
            return unescape(value)
    return None


def text_of(html: str, class_name: str) -> str | None:
    match = re.search(
        rf"""<(?P<tag>\w+)[^>]*\bclass=["'][^"']*\b{re.escape(class_name)}\b[^"']*["'][^>]*>(?P<body>.*?)</(?P=tag)>""",
        html,
        re.I | re.S,
    )
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", match.group("body"))
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    return text or None


def parse_count(raw: str | None) -> int | None:
    if not raw:
        return None
    match = _COUNT_NUM.search(raw.replace("\xa0", " "))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_relative_date(raw: str | None, *, now: datetime | None = None) -> str | None:
    """Turn '21 hours ago' into an approximate UTC ISO timestamp.

    Relative dates are only a filename prefix; they are not used as a dedup key.
    Unparseable strings are dropped rather than stored, so a later run can fill
    them in if the source starts emitting real timestamps.
    """
    if not raw:
        return None
    text = unescape(raw).strip()
    match = _RELATIVE_DATE.search(text)
    if not match:
        return None
    n = int(match.group("n"))
    unit = match.group("unit").lower()
    delta = {
        "second": timedelta(seconds=n),
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=30 * n),
        "year": timedelta(days=365 * n),
    }[unit]
    stamp = (now or datetime.now(UTC)) - delta
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chunk_by_start(html: str, start_re: re.Pattern[str]) -> list[str]:
    starts = [m.start() for m in start_re.finditer(html)]
    chunks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        chunks.append(html[start:end])
    return chunks


def pick_media_url(chunk: str, base: str) -> str | None:
    """Prefer the explicit download href, then a non-lazy image."""
    for match in re.finditer(r"""<(?P<tag>a|img|video|source)\b(?P<attrs>[^>]*)>""", chunk, re.I):
        tag = match.group("tag").lower()
        attrs = match.group("attrs")
        classes = " ".join(re.findall(r"""\bclass=["']([^"']+)["']""", attrs, re.I)).lower()
        href = first_attr(attrs, "href")
        src = first_attr(attrs, "data-src", "src")
        if tag == "a" and href and ("download" in classes or "downbtn" in classes):
            url = clean_url(href, base)
            if url:
                return url
        if tag in {"video", "source"} and src:
            url = clean_url(src, base)
            if url and is_direct_media(url):
                return url
    for match in re.finditer(r"""<img\b(?P<attrs>[^>]*)>""", chunk, re.I):
        src = first_attr(match.group("attrs"), "data-src", "src")
        url = clean_url(src, base)
        if not url:
            continue
        if any(token in url.lower() for token in LAZY_PLACEHOLDERS):
            continue
        if is_direct_media(url):
            return url
    return None


def is_video_chunk(chunk: str) -> bool:
    lowered = chunk.lower()
    if "icon_video" in lowered or "icon_tv" in lowered:
        return True
    if re.search(r"""<(?:i|span|div)\b[^>]*\bclass=["'][^"']*\bvideo\b""", chunk, re.I):
        return True
    if re.search(r"""\b(?:down_video|is_video|type=["']video)""", chunk, re.I):
        return True
    href = first_attr(chunk, "href") or ""
    src = first_attr(chunk, "data-src", "src") or ""
    return bool(re.search(r"\.(?:mp4|mov|m4v|webm)(?:$|[?#])", href + " " + src, re.I))


def imginn_shortcode(href: str) -> str | None:
    match = _IMGINN_POST.search(href)
    if not match or is_template_noise(match.group(1)):
        return None
    return match.group(1)


def pixnoy_shortcode(href: str) -> str | None:
    match = _PIXNOY_POST.search(href)
    if not match or is_template_noise(match.group(1)):
        return None
    return match.group(1)


def parse_imginn_profile(html: str) -> dict[str, Any]:
    user_id = first_attr(html, "data-id") if 'class="userinfo"' in html or "userinfo" in html else None
    if not user_id:
        match = re.search(r"""class=["']userinfo["'][^>]*data-id=["']([^"']+)["']""", html, re.I)
        user_id = match.group(1) if match else first_attr(html, "data-id")
    display = text_of(html, "name") or text_of(html, "fullname")
    username = text_of(html, "username")
    if username:
        username = username.lstrip("@")
    return {
        "user_id": user_id,
        "display_name": display,
        "username": username,
        "media_total": parse_count(text_of(html, "total") or first_attr(html, "data-total")),
    }


def parse_pixnoy_profile(html: str) -> dict[str, Any]:
    user_id = None
    match = re.search(r"""<input[^>]*\bname=["']userid["'][^>]*\bvalue=["']([^"']+)["']""", html, re.I)
    if match:
        user_id = match.group(1)
    posts = None
    block = re.search(
        r"""class=["'][^"']*\bitem_posts\b[^"']*["'](?P<body>.*?)class=["'][^"']*\bitem_followers\b""",
        html,
        re.I | re.S,
    )
    if block:
        title = re.search(r"""title=["']([^"']+)["']""", block.group("body"))
        posts = parse_count(title.group(1) if title else text_of(block.group("body"), "num"))
    return {
        "user_id": user_id,
        "display_name": text_of(html, "fullname"),
        "username": (text_of(html, "username") or "").lstrip("@") or None,
        "media_total": posts,
    }


def parse_imginn_items(
    html: str, *, base: str, provider: str = "imginn"
) -> tuple[list[RemoteItem], str | None, str | None]:
    items: list[RemoteItem] = []
    seen: set[str] = set()
    for chunk in _chunk_by_start(html, re.compile(r"""<div\b[^>]*\bclass=["'][^"']*\bitem\b""", re.I)):
        href = first_attr(chunk, "href") or ""
        code = imginn_shortcode(href)
        if not code:
            # The download <a> is not the permalink; search the chunk.
            for candidate in re.findall(r'href=["\']([^"\']+)["\']', chunk, re.I):
                code = imginn_shortcode(candidate)
                if code:
                    href = candidate
                    break
        if not code or code in seen:
            continue
        seen.add(code)
        media = pick_media_url(chunk, base)
        fallback = f"{base.rstrip('/')}/p/{code}/"
        permalink = clean_url(href if imginn_shortcode(href) else f"/p/{code}/", base) or fallback
        url = media or permalink
        posted = parse_relative_date(text_of(chunk, "time"))
        media_type = "video" if is_video_chunk(chunk) else "image"
        items.append(
            RemoteItem(
                remote_id=code,
                url=url,
                media_type=media_type,
                provider=provider,
                posted_at=posted,
                filename_hint=code,
            )
        )
    cursor = None
    user_id = None
    more = re.search(
        r"""<(?:button|a|div)\b[^>]*\bclass=["'][^"']*\bload-more\b[^>]*>""",
        html,
        re.I,
    )
    if more:
        tag = more.group(0)
        cursor = first_attr(tag, "data-cursor") or None
        user_id = first_attr(tag, "data-id") or None
        if cursor and is_template_noise(cursor):
            cursor = None
    if not user_id:
        user_id = first_attr(html, "data-id")
    return items, cursor or None, user_id


def parse_pixnoy_items(
    html: str, *, base: str, provider: str = "pixnoy"
) -> tuple[list[RemoteItem], str | None, str | None]:
    items: list[RemoteItem] = []
    seen: set[str] = set()
    for chunk in _chunk_by_start(html, re.compile(r"""<div\b[^>]*\bclass=["'][^"']*\bpost_box\b""", re.I)):
        href = ""
        code = None
        for candidate in re.findall(r'href=["\']([^"\']+)["\']', chunk, re.I):
            code = pixnoy_shortcode(candidate)
            if code:
                href = candidate
                break
        if not code or code in seen:
            continue
        seen.add(code)
        media = pick_media_url(chunk, base)
        fallback = f"{base.rstrip('/')}/post/{code}/"
        permalink = clean_url(href if pixnoy_shortcode(href) else f"/post/{code}/", base) or fallback
        url = media or permalink
        posted = parse_relative_date(text_of(chunk, "txt") or text_of(chunk, "time"))
        media_type = "video" if is_video_chunk(chunk) else "image"
        items.append(
            RemoteItem(
                remote_id=code,
                url=url,
                media_type=media_type,
                provider=provider,
                posted_at=posted,
                filename_hint=code,
            )
        )
    cursor = None
    more = re.search(
        r"""<(?:a|button|div)\b[^>]*\bclass=["'][^"']*\bmore_btn\b[^>]*>""",
        html,
        re.I,
    )
    if more:
        cursor = first_attr(more.group(0), "data-next") or None
        if cursor and is_template_noise(cursor):
            cursor = None
    return items, cursor or None, None


def items_from_json_obj(payload: Any, *, base: str, provider: str) -> tuple[list[RemoteItem], str | None]:
    """Accept the handful of JSON shapes these mirrors have used."""
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return [], None
        if text[:1] in "{[":
            try:
                payload = json.loads(text)
            except ValueError:
                parser = parse_imginn_items if provider == "imginn" else parse_pixnoy_items
                items, cursor, _ = parser(text, base=base, provider=provider)
                return items, cursor
        else:
            parser = parse_imginn_items if provider == "imginn" else parse_pixnoy_items
            items, cursor, _ = parser(text, base=base, provider=provider)
            return items, cursor

    if not isinstance(payload, dict):
        if isinstance(payload, list):
            mapped = [_item_from_mapping(row, base=base, provider=provider) for row in payload if isinstance(row, dict)]
            return mapped, None
        return [], None

    cursor = _cursor_from_mapping(payload)
    raw_items = payload.get("items") or payload.get("html") or payload.get("data")
    posts = payload.get("posts")
    if raw_items is None and isinstance(posts, dict):
        cursor = cursor or _cursor_from_mapping(posts)
        raw_items = posts.get("items") or posts.get("html")
    elif raw_items is None:
        raw_items = posts

    if isinstance(raw_items, str):
        parser = parse_imginn_items if provider == "imginn" else parse_pixnoy_items
        items, nested_cursor, _ = parser(raw_items, base=base, provider=provider)
        return items, cursor or nested_cursor
    if isinstance(raw_items, list):
        items = []
        for row in raw_items:
            if isinstance(row, dict):
                item = _item_from_mapping(row, base=base, provider=provider)
                if item:
                    items.append(item)
            elif isinstance(row, str):
                parser = parse_imginn_items if provider == "imginn" else parse_pixnoy_items
                nested, _, _ = parser(row, base=base, provider=provider)
                items.extend(nested)
        return items, cursor
    return [], cursor


def _cursor_from_mapping(payload: dict[str, Any]) -> str | None:
    for key in ("cursor", "next", "next_cursor", "nextCursor", "data-next"):
        value = payload.get(key)
        if isinstance(value, str) and value and not is_template_noise(value):
            if payload.get("hasNext") is False or payload.get("has_next") is False:
                return None
            return value
    if payload.get("hasNext") is False or payload.get("has_next") is False or payload.get("more_available") is False:
        return None
    return None


def _item_from_mapping(row: dict[str, Any], *, base: str, provider: str) -> RemoteItem | None:
    code = (
        row.get("shortcode")
        or row.get("code")
        or row.get("id")
        or imginn_shortcode(str(row.get("url") or row.get("href") or ""))
        or pixnoy_shortcode(str(row.get("url") or row.get("href") or ""))
    )
    if not code or not isinstance(code, str) or is_template_noise(code):
        return None
    is_video = bool(row.get("is_video")) or str(row.get("type") or "").lower() in {"video", "igtv", "reel", "clips"}
    media = None
    for key in ("down_video", "down_pic", "download", "video_url", "src", "url", "display_url", "pic_p"):
        if key == "down_video" and not is_video:
            continue
        if key == "down_pic" and is_video and row.get("down_video"):
            continue
        media = clean_url(str(row[key]) if row.get(key) else None, base)
        if media:
            break
    path = "/p/" if provider == "imginn" else "/post/"
    permalink = clean_url(str(row.get("href") or f"{path}{code}/"), base) or f"{base.rstrip('/')}{path}{code}/"
    posted = None
    if isinstance(row.get("taken_at"), int):
        posted = (
            datetime.fromtimestamp(row["taken_at"], tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
    else:
        posted = parse_relative_date(str(row.get("ftime") or row.get("time") or row.get("date") or ""))
    return RemoteItem(
        remote_id=str(code),
        url=media or permalink,
        media_type="video" if is_video else "image",
        provider=provider,
        posted_at=posted,
        filename_hint=str(code),
    )


def extract_media_from_post_page(html: str, *, base: str) -> str | None:
    """Best-effort original file URL from a post permalink page."""
    video = re.search(r"""<(?:video|source)\b[^>]*\b(?:src|data-src)=["']([^"']+)["']""", html, re.I)
    if video:
        url = clean_url(video.group(1), base)
        if url and is_direct_media(url):
            return url
    download = re.search(
        r"""<a\b[^>]*\bclass=["'][^"']*\b(?:download|downbtn)\b[^"']*["'][^>]*\bhref=["']([^"']+)["']""",
        html,
        re.I,
    )
    if download:
        url = clean_url(download.group(1), base)
        if url:
            return url
    return pick_media_url(html, base)
