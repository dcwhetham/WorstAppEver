"""External link derivation for the account view.

Mirror sites appear and disappear constantly, so the template table is data
rather than logic — add a row and both the UI links and the scraper's fallback
order pick it up. `kind='derived'` links are regenerated freely; `kind='manual'`
links are user property and are never touched by a re-derive.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

# provider -> (label, url template, fallback rank)
PROVIDERS: dict[str, tuple[str, str, int]] = {
    "instagram": ("Instagram", "https://www.instagram.com/{handle}/", 10),
    "imginn": ("Imginn", "https://imginn.com/{handle}/", 20),
    "pixnoy": ("Pixnoy", "https://www.pixnoy.com/profile/{handle}/", 30),
    "picuki": ("Picuki", "https://www.picuki.com/profile/{handle}", 40),
    "dumpor": ("Dumpor", "https://dumpor.com/v/{handle}", 50),
    "sotwe": ("Sotwe", "https://www.sotwe.com/{handle}", 60),
}

# Providers created automatically for every new account.
DEFAULT_DERIVED = ("instagram", "imginn", "pixnoy")

_HOST_HINTS = {
    "instagram.com": "instagram",
    "imginn.com": "imginn",
    "pixnoy.com": "pixnoy",
    "picuki.com": "picuki",
    "dumpor.com": "dumpor",
    "sotwe.com": "sotwe",
    "twitter.com": "twitter",
    "x.com": "twitter",
}


def infer_provider(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    for suffix, provider in _HOST_HINTS.items():
        if host == suffix or host.endswith("." + suffix):
            return provider
    return "custom"


def build_url(provider: str, handle: str) -> str | None:
    entry = PROVIDERS.get(provider)
    return None if entry is None else entry[1].format(handle=handle)


def ensure_derived_links(conn: sqlite3.Connection, account_id: int, handle: str) -> int:
    """Create the standard provider links for an account. Idempotent.

    `INSERT OR IGNORE` against the unique `(account_id, url)` index means a
    manual link that happens to match a derived URL keeps its `kind='manual'`
    and its label.
    """
    created = 0
    for index, provider in enumerate(DEFAULT_DERIVED):
        label, _, rank = PROVIDERS[provider]
        url = build_url(provider, handle)
        if not url:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO account_links
                (account_id, provider, kind, url, label, remote_handle, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                provider,
                "primary" if index == 0 else "derived",
                url,
                label,
                handle,
                rank,
            ),
        )
        created += cursor.rowcount or 0
    return created


def add_manual_link(
    conn: sqlite3.Connection,
    account_id: int,
    url: str,
    *,
    provider: str | None = None,
    label: str | None = None,
    remote_handle: str | None = None,
    sort_order: int = 100,
) -> int | None:
    """Register a user-supplied mirror. Returns the new id, or None if duplicate."""
    resolved = provider or infer_provider(url)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO account_links
            (account_id, provider, kind, url, label, remote_handle, sort_order)
        VALUES (?, ?, 'manual', ?, ?, ?, ?)
        """,
        (
            account_id,
            resolved,
            url.strip(),
            label or PROVIDERS.get(resolved, (resolved.title(),))[0],
            remote_handle,
            sort_order,
        ),
    )
    return int(cursor.lastrowid) if cursor.rowcount else None
