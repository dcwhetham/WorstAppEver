# Media Archive

A self-hosted dashboard and management tool for a local media archive. Three
services share one SQLite file and a folder of raw files:

| Service | What it is | What breaks if it dies |
| --- | --- | --- |
| `web` | Next.js dashboard — the only user interface in the system | You lose the UI |
| `backend` | FastAPI: index, local scanner, hash dedup, raw streaming, ZIP bundling | The UI goes blank; files and scraping are untouched |
| `scraper` | Isolated worker that collects new media | The dashboard shows "worker offline" and nothing else changes |

The last row is the design constraint that shaped everything else. The scraper is
the component most likely to break — sites change markup, block IPs, and rate
limit without warning — so it shares **no code** with the backend and they never
call each other. All coordination happens through two tables, `scrape_jobs` and
`event_log`. You can delete the scraper container entirely and the archive stays
fully browsable, searchable, viewable and downloadable.

```
docker compose up -d --build
open http://localhost:3000
```

---

## 1. Directory tree

```
media-archive/
├── docker-compose.yml           # 3 services, 1 shared SQLite file, no broker
├── .env.example                 # deployment only — behaviour is configured in the GUI
├── .dockerignore                # keeps the archive out of the build context
│
├── db/                          # schema, owned by neither service
│   ├── README.md                # schema documentation and design rationale
│   ├── migrations/
│   │   ├── 0001_init.sql        # tables, constraints, indexes
│   │   ├── 0002_triggers.sql    # counter maintenance, timestamps, job side effects
│   │   └── 0003_views.sql       # dashboard read models
│   └── seed/
│       └── demo_seed.sql        # sample accounts, jobs and errors for development
│
├── backend/                     # API + index + scanner + dedup + bundling
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py              # FastAPI app, migrations on startup, lease reaping
│   │   ├── config.py            # env-driven settings
│   │   ├── db.py                # connection pool, WAL mode, migration runner
│   │   ├── models.py            # Pydantic request/response models
│   │   ├── scanner.py           # walks /archive, classifies, hashes, reconciles
│   │   ├── hashing.py           # SHA-256 + cheap size/mtime pre-filter
│   │   ├── streaming.py         # HTTP Range (206) so <video> can seek
│   │   ├── bundler.py           # content-addressed ZIP cache, stored (uncompressed)
│   │   ├── links.py             # derived provider links + manual mirrors
│   │   ├── logs.py              # structured writes to event_log, with coalescing
│   │   ├── util.py
│   │   ├── deps.py
│   │   ├── repositories/        # all SQL lives here
│   │   │   ├── accounts.py
│   │   │   ├── media.py
│   │   │   └── jobs.py          # enqueue / claim / lease / reap
│   │   └── routers/
│   │       ├── accounts.py      # CRUD, favorite, scrape toggle, run-now, bundle, links
│   │       ├── media.py         # listing, raw bytes, duplicates report
│   │       ├── jobs.py          # queue inspection, cancel, retry, worker status
│   │       ├── logs.py          # per-account error history for the log modal
│   │       └── system.py        # health, stats, settings, scan trigger
│   └── tests/
│       ├── test_api.py
│       └── test_scanner.py
│
├── scraper/                     # isolated collector. No shared code with backend.
│   ├── Dockerfile               # runs unprivileged, exposes no port
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── worker/
│   │   ├── main.py              # poll loop, heartbeat, scheduling, crash isolation
│   │   ├── config.py            # WorkerEnv (deployment) vs RuntimeSettings (from DB)
│   │   ├── db.py                # deliberate near-copy of the backend's, not an import
│   │   ├── queue.py             # lease-based job claiming, progress, cancellation
│   │   ├── scheduler.py         # run blocks, favorite revisit intervals, staleness order
│   │   ├── sync.py              # the incremental engine: 4 dedup guards, atomic writes
│   │   ├── pacing.py            # per-run budgets, jitter, ETA estimation
│   │   ├── hashing.py           # hash-while-writing
│   │   ├── net.py               # UA rotation, proxy rotation, cookie jars
│   │   ├── logs.py              # stderr + event_log
│   │   └── adapters/
│   │       ├── base.py          # SourceAdapter protocol + error taxonomy
│   │       ├── __init__.py      # fallback chain, ranked mirrors-before-origin
│   │       ├── fixture.py       # offline adapter for tests
│   │       └── mirror.py        # imginn / pixnoy / instagram stubs
│   └── tests/
│       ├── test_sync.py
│       └── test_pacing.py
│
├── web/                         # Next.js 15 App Router + Tailwind v4 + Framer Motion
│   ├── Dockerfile               # multi-stage, standalone output
│   ├── next.config.ts           # proxies /api to the backend (keeps media same-origin)
│   ├── package.json
│   └── src/
│       ├── app/
│       │   ├── globals.css      # Aeon Tajo theme tokens, 3D perspective utilities
│       │   ├── layout.tsx
│       │   └── page.tsx
│       ├── lib/
│       │   ├── types.ts         # mirrors the Pydantic models
│       │   ├── api.ts           # typed fetch client
│       │   ├── format.ts        # relative dates, byte sizes, durations
│       │   └── hooks.ts         # SWR with adaptive polling, selection, dismissal
│       └── components/
│           ├── Dashboard.tsx           # top-level state: filters, expansion, modals
│           ├── DashboardHeader.tsx     # archive stats, worker status, global actions
│           ├── FilterBar.tsx           # search + favorites/legacy/error filters + sort
│           ├── AccountGrid.tsx
│           ├── AccountCard.tsx         # 3D tilt physics  ← deliverable 3
│           ├── CardFooter.tsx          # counts, last scrape, quick toggles, ETA
│           ├── EtaProgress.tsx         # live countdown for new/backfilling accounts
│           ├── ExpandedAccount.tsx     # Netflix-style expansion  ← deliverable 3
│           ├── MediaGallery.tsx
│           ├── MediaViewer.tsx         # arrow / Home / End / Escape / Space
│           ├── LinkManager.tsx         # derived + manual mirror links
│           ├── LogViewerModal.tsx      # container errors without `docker logs`
│           ├── BatchActionBar.tsx      # bulk scrape / bundle / favorite
│           ├── AddAccountDialog.tsx
│           ├── icons.tsx
│           └── ui/
│               ├── Badge.tsx
│               └── Modal.tsx
│
└── archive/                     # your media. gitignored. see archive/README.md
    ├── someaccount/
    │   ├── photos/
    │   └── videos/
    └── another.handle/
        ├── photos/
        └── videos/
```

---

## 2. Database schema

Full DDL in [`db/migrations/`](db/migrations), documented in
[`db/README.md`](db/README.md). Both services apply the same migration files at
startup, idempotently, and the schema is versioned in a `schema_migrations`
table.

| Table | Purpose |
| --- | --- |
| `accounts` | Handle, status, platform state, favorite, scrape toggle, counters, timestamps |
| `account_links` | Derived provider links and user-added mirrors, with per-link health |
| `media_files` | One row per file on disk: path, size, `mtime_ns`, content hash, dimensions, provenance |
| `remote_index` | What the source advertises, independent of what has been fetched — the resume point |
| `scrape_jobs` | The queue: priority, lease, progress, ETA, cancellation |
| `event_log` | Error and event history with coalesced repeats, per account |
| `worker_heartbeats` | Worker liveness, rendered directly in the dashboard header |
| `settings` | Typed key/value config the GUI writes and the scraper reads each loop |

Views in `0003_views.sql` (`v_account_dashboard`, `v_scrape_queue`,
`v_archive_stats`) do the aggregation, so a card render is one indexed query
rather than a fan-out per account.

Six decisions worth calling out:

**Status and platform state are separate columns.** `status` is your
classification (`active` / `legacy` / `archived`) and `platform_state` is the
world's (`ok` / `unavailable` / `private` / `banned`). Collapsing them into one
enum means an account being banned upstream silently overwrites your own filing,
and you can no longer ask "which accounts did I mark legacy *before* they
vanished".

**Deduplication is a partial unique index, not application logic.**

```sql
CREATE UNIQUE INDEX idx_media_hash_unique
    ON media_files (account_id, content_hash)
    WHERE content_hash IS NOT NULL AND deleted_at IS NULL;
```

The database refuses to hold the same bytes twice for one account regardless of
which code path inserts them, so a bug in the scraper cannot produce duplicates.
When it fires during a local scan the second file is still indexed — it exists,
and hiding it would be a lie — with a NULL hash and `duplicate_of` pointing at the
row that kept it, which is what keeps it findable in the duplicates report.

**Deletes are soft.** A file you delete from disk becomes `is_missing = 1` rather
than a removed row, and the scraper reads that as "the user got rid of this
deliberately" and will not fetch it again. Without this, deleting something you
did not want is a request for it to come back tonight.

**`mtime_ns` sits next to `bytes`.** A rescan re-hashes only files whose size or
mtime changed. That is the difference between a scan taking a second and
re-reading every byte in the archive.

**Counters are maintained by triggers.** `image_count`, `video_count` and
`total_bytes` update inside the same transaction as the `media_files` write, so
the dashboard cannot show a count that disagrees with the table it summarises.

**One live job per account, enforced by a partial unique index.** Not by a lock
and not by application checks:

```sql
CREATE UNIQUE INDEX idx_jobs_one_active
    ON scrape_jobs (account_id, job_type)
    WHERE status IN ('queued', 'claimed', 'running');
```

Two workers claiming the same account is a schema violation, not a race.

---

## 3. Frontend: 3D tilt and Netflix-style expansion

Two components carry the interaction design. Both are worth reading in full —
[`AccountCard.tsx`](web/src/components/AccountCard.tsx) and
[`ExpandedAccount.tsx`](web/src/components/ExpandedAccount.tsx) — but the
non-obvious parts are these.

### Tilt: springs in both directions, on a separate node from the layout animation

Cards rest leaning *away* from the viewer and stand up under the cursor, so the
rotation crosses zero rather than merely shrinking toward it. That pass through
zero is what makes it read as standing up instead of wobbling.

```tsx
const REST_TILT_X = 7.5;   // leaning backward at rest
const LEAN_TILT_X = -5;    // leaning toward the viewer on hover
const TILT_SPRING = { stiffness: 260, damping: 26, mass: 0.6 };

const targetX = useMotionValue(REST_TILT_X);
const rotateX = useSpring(targetX, TILT_SPRING);   // the DOM reads the spring
```

Pointer handlers only ever set the *targets*; the springs interpolate. That is
what makes mouse-leave feel right: a CSS transition restarts from wherever the
pointer was, so flicking across a grid leaves cards gliding at visibly different
speeds, whereas a spring carries its velocity and every card settles identically.

The shadow is driven off the rotation rather than toggled on hover, because
rotation without a responsive shadow reads as a flat skew:

```tsx
const shadowStrength = useTransform(rotateX, [REST_TILT_X, LEAN_TILT_X], [0.35, 0.75]);
const boxShadow = useMotionTemplate`0 ${shadowSpread}px ${shadowSpread}px -12px rgba(2,6,12,${shadowStrength})`;
```

The structural detail that matters most: **the tilt layer and the
`layoutId` layer are different DOM nodes.**

```tsx
<div className="scene">                    {/* perspective lives here */}
  <motion.div style={{ rotateX, rotateY, translateZ: lift, boxShadow }}>
    <motion.article layoutId={`account-shell-${account.id}`}>
```

Framer Motion writes `transform` on a `layoutId` element to perform the morph. If
the tilt springs write to the same node, the two fight and the expansion visibly
stutters. Splitting them costs one wrapper div and the conflict disappears.
`perspective` must be on the *parent* — without it, `rotateX` is an orthographic
squash with no depth at all.

### Expansion: one shared element, and the card leaves a hole behind

Clicking morphs the card into a full detail view via a shared `layoutId`, with
the card's slot in the grid replaced by an invisible placeholder that preserves
its height. Otherwise the grid reflows underneath the animation and the card
appears to fly toward a target that is still moving.

```tsx
{isExpanded ? (
  <div aria-hidden className="invisible rounded-2xl" style={{ aspectRatio: "4 / 5" }} />
) : (
  <motion.article layoutId={`account-shell-${account.id}`} /* … */ />
)}
```

Tilt is also suppressed while expanded, since a detail panel that leans under the
cursor is nauseating rather than tactile.

Every animation is gated on `usePrefersReducedMotion()`, which drops the motion
styles entirely instead of shortening the durations.

### Elsewhere in the UI

- **ETA timer** — `EtaProgress` counts down locally at 1 Hz and re-syncs against
  the server value, so the number moves smoothly without polling per second.
  Shown for new accounts and for backfills above the pacing threshold.
- **Adaptive polling** — SWR refreshes fast while a job is running and slows to a
  crawl when the queue is idle, so an open tab is not a load generator.
- **Media viewer** — arrows page, `Home`/`End` jump, `Space` toggles video,
  `Escape` closes. Videos are `<video src>` pointed at the raw endpoint, and
  seeking works because the backend answers Range requests properly.
- **Log modal** — per-account `event_log` with level filters and repeat
  coalescing, so diagnosing a scrape failure never means `docker logs`.

---

## 4. Scraper: incremental, paced, hash-deduplicated

Entry points: [`sync.py`](scraper/worker/sync.py) for the engine,
[`pacing.py`](scraper/worker/pacing.py) for the throttling,
[`scheduler.py`](scraper/worker/scheduler.py) for rotation,
[`main.py`](scraper/worker/main.py) for the loop.

The split between the scheduler and the pacer is deliberate: the scheduler
decides *which* accounts get looked at and when, the pacer decides *how much*
each visit does. So a change to throttling can never affect rotation fairness,
and vice versa.

### Never redownload: four guards, cheapest first

```
1. remote_id already in media_files          → free
2. remote-advertised hash already held       → free
3. file already at the target path on disk   → one local read, adopt it into the index
4. post-download hash collision              → discard the .part, nothing enters the archive
```

Only guard 4 spends bandwidth, and only for remote ids never seen before. Guard 3
is what makes a manually populated archive work: drop files in, and the scraper
adopts them rather than fetching duplicates alongside them.

Downloads are atomic. Bytes stream to `<name>.part` while being hashed in the
same pass, and the rename into place happens only after the hash succeeds — so a
killed container leaves a stray `.part` and never a truncated file in the
archive. Nothing is transcoded, re-containered, or stripped of metadata.

### Self-pacing

`plan_run` decides a per-run budget and a delay between items from the backlog
size, whether this is a first sync, whether the account is a favorite, and how
many consecutive failures it has. A 900-file first sync becomes a slow drip
across many runs instead of one burst that gets the IP flagged; new accounts ramp
up over their first several runs rather than starting at full rate.

Delays are jittered around the base rather than fixed, because a request every
exactly-8.0-seconds is a more distinctive signature than a fast one. When a run
exhausts its budget with work remaining, it reports `requeue_after_seconds` and
the worker queues the follow-up *after* the current job reaches a terminal state —
`idx_jobs_one_active` forbids a second live job for the account, so the engine
cannot enqueue it while still holding the lease.

### Scheduling

Favorites are checked more *often*, not harder: a shorter revisit interval and a
queue priority boost, but the same per-run budget, because the point is freshness
rather than volume. Legacy accounts are revisited weekly — a frozen archive
checked daily is pure noise. Ties break on staleness, which stops a handful of
favorites monopolising every block while a long tail never gets visited.

Scheduled work only runs inside the configured block, and each cycle queues a
capped number of accounts staggered by minutes rather than releasing them all the
instant the clock ticks over. Manual **Run Now** jobs ignore the block entirely —
someone clicking a button at 3pm should not be told to wait until 2am.

### Resilience

Adapters are tried in a ranked chain with mirrors before the origin, because a
failed request against a mirror costs nothing. Adapter-level failures (not
implemented, missing cookies) fall through to the next; account-level failures
(deleted, private) stop the walk, since trying six mirrors for an account that no
longer exists is both pointless and conspicuous.

Rate limits become a timed deferral, not a retry loop. Blocks and unavailable
accounts update `platform_state` so the dashboard shows *why* an account stopped
updating. Proxy and cookie rotation are hooks with honest empty defaults — no
proxies configured means a direct connection rather than a pretence of stealth.

### Zero user intervention

The container ships with no configuration file. `WorkerEnv` holds deployment
facts (paths, proxies) and `RuntimeSettings` is re-read from the `settings` table
on every loop iteration, so a toggle flipped in the browser takes effect on the
next pass without a restart. Schedule window, delays, budgets, thresholds and
per-account enable/disable are all GUI-side.

---

## Running it

### Docker (all three services)

```bash
cp .env.example .env          # optional; every value has a working default
docker compose up -d --build
open http://localhost:3000
```

Point `ARCHIVE_HOST_PATH` at wherever you actually want tens of gigabytes to
live. It is a bind mount, not a named volume, precisely so the files stay
directly openable in your own file manager and video player.

### Local development

```bash
python -m venv .venv && source .venv/bin/activate

# backend  → http://localhost:8000  (OpenAPI docs at /docs)
pip install -r backend/requirements.txt -e 'backend[dev]'
cd backend && uvicorn app.main:app --reload

# scraper (separate shell, same venv)
pip install -r scraper/requirements.txt -e 'scraper[dev]'
cd scraper && python -m worker.main

# web      → http://localhost:3000
cd web && npm install && npm run dev
```

Migrations run automatically on startup for both Python services. To load sample
accounts, jobs and error history:

```bash
sqlite3 data/archive.db < db/seed/demo_seed.sql
```

### Tests

```bash
(cd backend && python -m pytest)   # scanner, dedup, API surface
(cd scraper && python -m pytest)   # sync engine, dedup guards, pacing across runs
(cd web && npm run lint && npx tsc --noEmit && npm run build)
```

The scraper suite runs the whole engine — discovery, hashing, dedup, pacing,
requeueing across multiple runs — against a fixture adapter with millisecond
delays, so it exercises the real code paths with no network access.

---

## A note on scope

The mirror adapters in `scraper/worker/adapters/mirror.py` are deliberate stubs.
They raise `AdapterUnavailableError`, which the fallback chain handles as
designed, so the full system runs end to end today against the fixture adapter.
Implementing them means writing request and parsing logic for a specific site,
which is a decision about what you are allowed to collect and at what rate — not
something to bury in scaffolding. `base.py` defines the protocol and the error
taxonomy; the engine, pacing, dedup, retry and reporting around it are complete.

Only collect what you have the right to collect, and respect the terms and rate
limits of any source you point this at.
