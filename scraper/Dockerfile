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

# Run unprivileged. This process writes only to /archive and /data, and a
# scraper parsing untrusted remote HTML is the last thing that should own root.
RUN useradd --create-home --uid 10001 worker
USER worker

# No port and no healthcheck, both intentional. The worker exposes no interface
# of any kind — it reports liveness by writing to `worker_heartbeats`, which the
# dashboard reads and renders, and it holds a job lease while running so an
# unclean stop lets the lease expire instead of losing the job.
CMD ["python", "-m", "worker.main"]
