# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# /backend — API, local scanner, hash dedup, bundler.
#
# Build context is the repo root, not this directory, because `db/migrations`
# has to come along: the schema is shared with the scraper and neither service
# owns it.
#
# No compiler toolchain is installed because nothing here needs one. The
# dependency set is pure Python and the heavy lifting (hashing, zipping, range
# requests) is all stdlib.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# The image reproduces the repo layout under /srv, so `Settings.REPO_ROOT`
# resolves to /srv and every default path lands where you would expect without
# an env override.
WORKDIR /srv/backend

# Requirements are copied alone so the dependency layer is rebuilt only when
# they change, not on every source edit.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY db/migrations/ /srv/db/migrations/
COPY backend/ /srv/backend/

# Defaults target the volumes declared in docker-compose.yml. Overriding these
# is how you point the same image at a different archive.
ENV ARCHIVE_DB_PATH=/data/archive.db \
    ARCHIVE_ROOT=/archive \
    MIGRATIONS_DIR=/srv/db/migrations \
    BUNDLE_CACHE_DIR=/data/bundles

EXPOSE 8000

# A red probe here means the database is unmigrated or the archive mount is
# missing — precisely the conditions that should keep this container out of
# rotation rather than let it serve an empty dashboard.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/system/health', timeout=4).status == 200 else 1)"

# One worker, deliberately. SQLite in WAL mode is happy with concurrent readers,
# but several uvicorn workers would each run migrations at startup and race each
# other for the schema lock.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
