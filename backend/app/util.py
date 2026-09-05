"""Small shared helpers: timestamps, name normalisation, safe path joins."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"

# Instagram-style handles: letters, digits, dot, underscore, hyphen. Anything
# else is rejected rather than transliterated, because the account name is also
# a directory name and silent rewriting would orphan folders.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Match the format SQLite's `strftime('%Y-%m-%dT%H:%M:%fZ')` produces.

    Mixing formats would make string comparisons on timestamp columns (which is
    how every ORDER BY here works) silently wrong.
    """
    return utc_now().strftime(ISO_FORMAT)[:-3] + "Z"


def iso_offset(seconds: float) -> str:
    from datetime import timedelta

    stamp = utc_now() + timedelta(seconds=seconds)
    return stamp.strftime(ISO_FORMAT)[:-3] + "Z"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_valid_account_name(name: str) -> bool:
    return bool(_HANDLE_RE.match(name)) and name not in {".", ".."}


def sanitize_filename(name: str, fallback: str = "file") -> str:
    cleaned = _UNSAFE_SEGMENT.sub("_", name).strip("._") or fallback
    return cleaned[:180]


def safe_join(root: Path, *parts: str) -> Path:
    """Join under `root`, refusing anything that escapes it.

    Media paths reach this from the database and from query strings, so
    `../../etc/passwd` has to be impossible rather than merely unlikely.
    """
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes archive root: {candidate}")
    return candidate


def human_bytes(count: int | None) -> str:
    if not count:
        return "0 B"
    step = 1024.0
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"
