"""Adapter registry and fallback chain.

Order is by `rank`, and the ranking encodes a policy rather than a preference:

* `fixture` (0) — offline test source, inert unless `SCRAPER_FIXTURE_DIR` is set.
* `imginn` (20), `pixnoy` (30) — mirrors. Tried first because a failure against a
  mirror costs nothing.
* `instagram` (90) — the canonical source, tried last. It is the one needing
  authentication and the one where aggressive access has real consequences.

The engine walks the chain until one adapter succeeds. Adapter-level failures
(unimplemented, missing cookies) move to the next; account-level failures
(deleted, private) stop the walk, because trying six mirrors for an account that
no longer exists is pointless and conspicuous.
"""

from __future__ import annotations

from pathlib import Path

from ..config import RuntimeSettings, WorkerEnv
from .base import SourceAdapter
from .fixture import FixtureAdapter
from .mirror import ImginnAdapter, InstagramAdapter, PixnoyAdapter

__all__ = ["build_chain", "SourceAdapter"]


def build_chain(env: WorkerEnv, settings: RuntimeSettings) -> list[SourceAdapter]:
    """Instantiate the adapter chain for this run.

    Rebuilt per job rather than cached, so each job gets a fresh HTTP client and
    therefore a fresh proxy from the rotator — a single long-lived client would
    pin every account to the same exit IP.
    """
    proxies = env.proxies if settings.proxy_rotation else []
    cookie_dir: Path = env.cookie_dir

    chain: list[SourceAdapter] = [FixtureAdapter()]
    for adapter_cls in (ImginnAdapter, PixnoyAdapter, InstagramAdapter):
        chain.append(
            adapter_cls(
                proxies=proxies,
                cookie_dir=cookie_dir,
                timeout=env.request_timeout_seconds,
                rotate_user_agent=settings.user_agent_rotation,
            )
        )
    return sorted(chain, key=lambda adapter: adapter.rank)
