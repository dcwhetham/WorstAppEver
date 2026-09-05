# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# /scraper — isolated collection worker.
#
# This image shares no Python code with /backend. The entire contract between
# them is the SQLite file and the migrations that shape it, so this container can
# crash-loop, be rebuilt, or be deleted outright and the dashboard degrades to
# "worker offline" instead of breaking.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/scraper

COPY scraper/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY db/migrations/ /srv/db/migrations/
COPY scraper/ /srv/scraper/

ENV ARCHIVE_DB_PATH=/data/archive.db \
    ARCHIVE_ROOT=/archive \
    MIGRATIONS_DIR=/srv/db/migrations \
    COOKIE_DIR=/data/cookies

# Runs as root by default. That is a deliberate trade: the worker must write the
# SQLite file (and its WAL sidecars) on the shared `archive-data` volume and
# create account folders on the host bind-mount. A named volume is root-owned on
# first boot, and `./archive` is owned by whatever uid ran compose — uid 10001
# can do neither, so the process died before its first heartbeat and the
# dashboard reported "Scraper never connected".
#
# Pin a host-matching uid with `user: "${PUID}:${PGID}"` in compose if you want
# the files to come out owned by you rather than root.
#
# No port and no healthcheck, both intentional. Liveness is `worker_heartbeats`.
CMD ["python", "-m", "worker.main"]
