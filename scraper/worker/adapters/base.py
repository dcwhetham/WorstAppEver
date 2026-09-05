"""The adapter contract.

An adapter's only job is to answer two questions about one account:

  * `list_items()` — what exists remotely right now?
  * `open_stream(item)` — give me the bytes of this one item.

Everything else — pacing, deduplication, retries, where files land, what gets
logged — belongs to the sync engine. Keeping adapters this thin is what makes
them disposable: mirror sites change their markup constantly, and when one
breaks it should be a single file to rewrite, with no risk to the archive.

`list_items()` yields newest-first. The engine relies on that ordering for its
early-exit heuristic: once it has seen enough consecutive already-known items it
stops paging, which turns a routine sync into one or two requests instead of a
full re-enumeration.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import IO, Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteItem:
    """One media object as seen at the source.

    `remote_id` must be stable for the same underlying media across runs — it is
    the cheapest dedup key we have. If a source only exposes a CDN URL that
    rotates its query string, derive the id from the path, never the full URL,
    or every run will look like an account full of new files.
    """

    remote_id: str
    url: str
    media_type: str  # 'image' | 'video' | 'other'
    provider: str

    # Optional metadata. `content_hash`/`size_bytes` come from an ETag or
    # Content-Length when the source provides one, and let the engine skip a
    # download before spending any bandwidth on it.
    content_hash: str | None = None
    size_bytes: int | None = None
    posted_at: str | None = None
    filename_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileInfo:
    """Cheap profile probe result: is the account reachable, and how big is it?

    `media_total` populates `accounts.expected_*`, which is what gives a brand
    new account an ETA denominator before discovery has finished.
    """

    handle: str
    exists: bool
    is_private: bool = False
    media_total: int | None = None
    image_total: int | None = None
    video_total: int | None = None
    display_name: str | None = None


class AdapterError(RuntimeError):
    """Base for adapter failures. Carries whether a retry could plausibly help."""

    retryable = True
    event = "adapter_error"


class RateLimitedError(AdapterError):
    """Source asked us to slow down. Honour `retry_after` when it is provided."""

    retryable = True
    event = "rate_limited"

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AccountUnavailableError(AdapterError):
    """Deleted, suspended, or 404 on every mirror.

    Not retryable: hammering a deleted account is exactly the pattern that gets
    an IP blocked, and it will never succeed.
    """

    retryable = False
    event = "account_unavailable"


class AccountPrivateError(AdapterError):
    """Exists but is not visible with our current credentials."""

    retryable = False
    event = "account_private"


class BlockedError(AdapterError):
    """Captcha, login wall, or a bot-detection interstitial.

    Retryable, but only after a long cool-off and ideally a different
    proxy/cookie identity, so the engine treats it as a hard stop for this run.
    """

    retryable = True
    event = "blocked"


class AdapterUnavailableError(AdapterError):
    """This adapter cannot run at all (missing cookies, unimplemented).

    Distinct from the errors above because it says nothing about the account —
    the engine should move straight to the next adapter in the chain.
    """

    retryable = False
    event = "adapter_unavailable"


@runtime_checkable
class SourceAdapter(Protocol):
    """What the sync engine requires of a source.

    A Protocol rather than a base class so a fallback adapter can be a thin
    object, a test fixture, or an out-of-tree plugin without inheriting anything.
    """

    name: str
    #: Lower is tried first in the fallback chain.
    rank: int

    def supports(self, handle: str, links: list[dict[str, Any]]) -> bool:
        """Whether this adapter has a usable URL for the account."""
        ...

    def probe(self, handle: str, links: list[dict[str, Any]]) -> ProfileInfo:
        """Cheap reachability/size check. One request where possible."""
        ...

    def list_items(
        self,
        handle: str,
        links: list[dict[str, Any]],
        *,
        max_items: int | None = None,
        since: str | None = None,
    ) -> Iterator[RemoteItem]:
        """Yield items newest-first.

        A generator, not a list: the engine stops consuming as soon as its
        early-exit condition is met, so pages are never fetched needlessly.
        """
        ...

    def open_stream(self, item: RemoteItem) -> AbstractContextManager[IO[bytes]]:
        """Open a binary read stream for one item.

        A context manager returning a file-like object, so the engine can hash
        and write in a single pass without ever holding the whole file in memory.
        """
        ...
