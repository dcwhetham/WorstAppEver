"""FastAPI application entrypoint.

Deliberately knows nothing about the scraper. It reads and writes the shared
SQLite database and serves the archive; whether a worker is running is something
it observes through heartbeats, never something it depends on.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import connect, migrate
from .embed import start_embedded_scraper, stop_embedded_scraper
from .repositories import jobs as jobs_repo
from .routers import accounts, jobs, logs, media, system

logger = logging.getLogger("archive.backend")

API_TITLE = "Media Archive API"
API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.archive_root.mkdir(parents=True, exist_ok=True)
    settings.bundle_cache_dir.mkdir(parents=True, exist_ok=True)

    applied = migrate(settings)
    if applied:
        logger.info("applied migrations: %s", ", ".join(applied))

    # A restart is the most likely reason for an orphaned lease, so reap once at
    # boot instead of waiting for someone to open the queue view.
    conn = connect(settings)
    try:
        reaped = jobs_repo.reap_expired_leases(conn)
        if reaped:
            logger.warning("reclaimed %d job(s) with expired leases", reaped)
    finally:
        conn.close()

    logger.info("archive root: %s", settings.archive_root)
    logger.info("database: %s", settings.db_path)

    # Optional. Off in Docker (the scraper service is the worker there) and on
    # for `make backend`, so a two-terminal local setup still heartbeats.
    scraper = start_embedded_scraper()
    yield
    stop_embedded_scraper(scraper)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        summary="Self-hosted media archive dashboard backend",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # The client reads pagination and bundle metadata from headers, which
        # CORS hides unless they are explicitly exposed.
        expose_headers=["X-Total-Count", "X-Bundle-File-Count", "X-Bundle-Cached", "Content-Range"],
    )

    prefix = settings.api_prefix
    app.include_router(system.router, prefix=prefix)
    app.include_router(accounts.router, prefix=prefix)
    app.include_router(accounts.batch_router, prefix=prefix)
    app.include_router(media.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(jobs.workers_router, prefix=prefix)
    app.include_router(logs.router, prefix=prefix)

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        # Mostly `safe_join` refusing a path that escapes the archive root. That
        # is a 400, not an unhandled 500, and it should never leak the path.
        logger.warning("rejected request %s: %s", request.url.path, exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": API_TITLE, "version": API_VERSION, "docs": "/docs", "api": prefix}

    return app


app = create_app()
