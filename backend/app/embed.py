"""Optional in-process scraper supervisor.

The architecture still holds: this starts the *same* `worker.main` entrypoint as
a sibling process, talking only through the database. It exists because the
common local path is `uvicorn` plus `next dev`, and nobody starts the third
terminal — which is how adding an account produced a queued job and a dashboard
that said "Scraper never connected".

Disabled inside the backend Docker image (`EMBED_SCRAPER=0` in compose) so the
dedicated scraper container is the only worker there.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from .config import REPO_ROOT

logger = logging.getLogger("archive.backend")

_TRUTHY = {"1", "true", "yes", "on"}


def embed_enabled() -> bool:
    return os.getenv("EMBED_SCRAPER", "").strip().lower() in _TRUTHY


def start_embedded_scraper() -> subprocess.Popen[bytes] | None:
    """Spawn `python -m worker.main` if requested and the scraper tree is present."""
    if not embed_enabled():
        return None

    scraper_root = REPO_ROOT / "scraper"
    if not (scraper_root / "worker" / "main.py").is_file():
        logger.warning("EMBED_SCRAPER is set but %s has no worker package", scraper_root)
        return None

    env = os.environ.copy()
    # worker.main is importable as `worker` only when the scraper root is on
    # sys.path. The dedicated container sets WORKDIR; this has to do it here.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(scraper_root) + (os.pathsep + existing if existing else "")

    process = subprocess.Popen(
        [sys.executable, "-m", "worker.main"],
        cwd=str(scraper_root),
        env=env,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    logger.info("embedded scraper started (pid %s, db still %s)", process.pid, env.get("ARCHIVE_DB_PATH", "default"))
    return process


def stop_embedded_scraper(process: subprocess.Popen[bytes] | None, timeout: float = 8.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
