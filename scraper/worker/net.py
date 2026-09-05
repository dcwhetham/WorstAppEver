"""HTTP session construction, and the identity rotation hooks.

Rotation is deliberately a *hook*, not an implementation. Baking in a proxy
provider or a cookie-harvesting flow would tie the scraper to whatever service
was fashionable when it was written. Instead:

* Proxies come from `SCRAPER_PROXIES` (comma separated). Empty means direct.
* Cookies come from Netscape-format `cookies.txt` files in `COOKIE_DIR`, one per
  provider, which is what every browser extension already exports.
* User agents rotate from a small pinned list, held *consistent for the life of a
  session*. Rotating the UA per request is worse than not rotating at all — real
  browsers do not change identity mid-scroll.

`httpx` is imported lazily so the rest of the worker (queue, pacing, sync,
tests) imports cleanly in an environment without it, and a missing dependency
surfaces as `AdapterUnavailableError` on the adapter that needed it rather than
an ImportError at boot.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from .adapters.base import (
    AccountUnavailableError,
    AdapterError,
    AdapterUnavailableError,
    BlockedError,
    RateLimitedError,
)

# Pinned, plausible desktop UAs. A short honest list beats a long list of
# fingerprints that contradict the TLS handshake they arrive on.
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
)

# Headers a real browser always sends. Their absence is a cheaper bot signal than
# any user-agent string.
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Status codes mapped to the exceptions the sync engine knows how to handle.
_RATE_LIMIT_CODES = {429}
_BLOCK_CODES = {401, 403, 405, 503}
_GONE_CODES = {404, 410}


class ProxyRotator:
    """Round-robin over the configured proxies.

    Round-robin rather than random so a bad proxy is hit predictably and shows up
    quickly in the logs, instead of intermittently for weeks.
    """

    def __init__(self, proxies: list[str]) -> None:
        self._proxies = list(proxies)
        self._cycle: Iterator[str] | None = itertools.cycle(self._proxies) if self._proxies else None

    @property
    def enabled(self) -> bool:
        return self._cycle is not None

    def next(self) -> str | None:
        return next(self._cycle) if self._cycle else None


def load_cookies(cookie_dir: Path, provider: str) -> dict[str, str]:
    """Read `<cookie_dir>/<provider>.txt` in Netscape format.

    Missing files are normal and return `{}` — most mirrors need no cookies at
    all, and the adapter decides whether it can proceed without them.
    """
    path = cookie_dir / f"{provider}.txt"
    if not path.is_file():
        return {}

    jar: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            jar[fields[5]] = fields[6]
    return jar


class HttpClient:
    """Thin wrapper mapping transport and status errors onto adapter exceptions.

    The mapping is the valuable part: the sync engine reacts very differently to
    a 429 (defer briefly), a 403 (long cool-off, rotate identity) and a 404
    (flag the account, stop scraping it). Adapters get that behaviour for free.
    """

    def __init__(
        self,
        *,
        provider: str,
        timeout: float = 45.0,
        proxies: list[str] | None = None,
        cookie_dir: Path | None = None,
        rotate_user_agent: bool = True,
    ) -> None:
        self.provider = provider
        self.timeout = timeout
        self._rotator = ProxyRotator(proxies or [])
        self._cookies = load_cookies(cookie_dir, provider) if cookie_dir else {}
        # Chosen once per client, then held: a session that changes its UA
        # halfway through looks less human, not more.
        self._user_agent = random.choice(USER_AGENTS) if rotate_user_agent else USER_AGENTS[0]
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - depends on install
            raise AdapterUnavailableError(
                "httpx is not installed; install scraper requirements to enable network adapters"
            ) from exc

        proxy = self._rotator.next()
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            # HTTP/2 because that is what a browser negotiates; an HTTP/1.1-only
            # client stands out regardless of its headers.
            http2=True,
            headers={**BASE_HEADERS, "User-Agent": self._user_agent},
            cookies=self._cookies or None,
            proxy=proxy,
        )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _raise_for_status(self, status: int, url: str, headers: dict[str, str]) -> None:
        if status in _RATE_LIMIT_CODES:
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else None
            except ValueError:
                wait = None
            raise RateLimitedError(f"{status} from {url}", retry_after=wait)
        if status in _GONE_CODES:
            raise AccountUnavailableError(f"{status} from {url}")
        if status in _BLOCK_CODES:
            raise BlockedError(f"{status} from {url} — likely bot detection or a login wall")
        if status >= 400:
            raise AdapterError(f"{status} from {url}")

    def get_text(self, url: str, **kwargs: Any) -> str:
        client = self._ensure_client()
        try:
            response = client.get(url, **kwargs)
        except Exception as exc:  # httpx transport errors
            raise AdapterError(f"request to {url} failed: {exc}") from exc
        self._raise_for_status(response.status_code, url, dict(response.headers))
        return response.text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        client = self._ensure_client()
        try:
            response = client.get(url, headers={"Accept": "application/json"}, **kwargs)
        except Exception as exc:
            raise AdapterError(f"request to {url} failed: {exc}") from exc
        self._raise_for_status(response.status_code, url, dict(response.headers))
        try:
            return response.json()
        except ValueError as exc:
            raise AdapterError(f"{url} returned non-JSON body") from exc

    @contextmanager
    def stream(self, url: str, **kwargs: Any) -> Iterator[IO[bytes]]:
        """Open a streaming download.

        Yields a file-like object so `HashingWriter` can hash and write in one
        pass; the full response is never materialised in memory.
        """
        client = self._ensure_client()
        try:
            with client.stream("GET", url, **kwargs) as response:
                self._raise_for_status(response.status_code, url, dict(response.headers))
                yield _ResponseReader(response)
        except (RateLimitedError, BlockedError, AccountUnavailableError, AdapterError):
            raise
        except Exception as exc:
            raise AdapterError(f"streaming {url} failed: {exc}") from exc


class _ResponseReader:
    """Adapts an httpx streaming response to the `read(n)` interface.

    httpx exposes chunk iteration rather than `read(n)`, so this buffers just
    enough to satisfy each call without accumulating the whole body.
    """

    def __init__(self, response: Any) -> None:
        self._iterator = response.iter_bytes()
        self._buffer = bytearray()
        self._exhausted = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            for chunk in self._iterator:
                self._buffer.extend(chunk)
            self._exhausted = True
            out, self._buffer = bytes(self._buffer), bytearray()
            return out

        while len(self._buffer) < size and not self._exhausted:
            try:
                self._buffer.extend(next(self._iterator))
            except StopIteration:
                self._exhausted = True
        out = bytes(self._buffer[:size])
        del self._buffer[:size]
        return out
