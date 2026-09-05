"""End-to-end sync engine tests, run against the offline fixture adapter.

These are the tests that matter most in this repo. "Never redownload everything"
and "never hammer the source" are the two properties the whole scraper exists to
guarantee, and they are only meaningfully verifiable by running the real engine
over a real filesystem — which is exactly what the fixture adapter enables.
"""

from __future__ import annotations

from conftest import make_account, make_fixture_files, queue_job, run_sync


def test_first_sync_downloads_and_files_by_media_type(conn, env, settings):
    make_fixture_files(env, "alpha", images=2, videos=1)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")

    result = run_sync(conn, env, settings, queue_job(conn, account_id))

    assert result.status == "succeeded"
    assert result.downloaded == 3

    # Raw files, split into photos/ and videos/ under the account folder.
    assert len(list((env.archive_root / "alpha" / "photos").glob("*.jpg"))) == 2
    assert len(list((env.archive_root / "alpha" / "videos").glob("*.mp4"))) == 1

    counts = conn.execute("SELECT image_count, video_count FROM accounts WHERE id = ?", (account_id,)).fetchone()
    assert (counts["image_count"], counts["video_count"]) == (2, 1)


def test_second_sync_downloads_nothing(conn, env, settings):
    """The headline guarantee: a re-run over unchanged content is a no-op."""
    make_fixture_files(env, "alpha", images=3)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")

    first = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert first.downloaded == 3

    second = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert second.downloaded == 0
    assert second.message == "Already up to date"
    assert conn.execute("SELECT COUNT(*) FROM media_files WHERE account_id = ?", (account_id,)).fetchone()[0] == 3


def test_only_new_items_are_fetched_on_a_later_run(conn, env, settings):
    make_fixture_files(env, "alpha", images=3)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    run_sync(conn, env, settings, queue_job(conn, account_id))

    fixture_dir = env.archive_root.parent / "fixtures" / "alpha" / "photos"
    (fixture_dir / "img900.jpg").write_bytes(b"a-brand-new-image-payload" * 4)

    result = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert result.downloaded == 1
    assert conn.execute("SELECT COUNT(*) FROM media_files WHERE account_id = ?", (account_id,)).fetchone()[0] == 4


def test_identical_bytes_under_a_new_remote_id_are_deduplicated(conn, env, settings):
    """Guard 4: the duplicate is detected after transfer and never enters the archive."""
    make_fixture_files(env, "alpha", images=1)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    run_sync(conn, env, settings, queue_job(conn, account_id))

    fixture_dir = env.archive_root.parent / "fixtures" / "alpha" / "photos"
    original = (fixture_dir / "img000.jpg").read_bytes()
    (fixture_dir / "reposted.jpg").write_bytes(original)

    result = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert result.downloaded == 0
    assert result.skipped_duplicate == 1

    # One row, one file: the copy is recognised, not archived twice.
    assert conn.execute("SELECT COUNT(*) FROM media_files WHERE account_id = ?", (account_id,)).fetchone()[0] == 1
    assert len(list((env.archive_root / "alpha" / "photos").glob("*.jpg"))) == 1

    state = conn.execute("SELECT state, last_error FROM remote_index WHERE remote_id LIKE '%reposted%'").fetchone()
    assert state["state"] == "duplicate"
    assert "hash matched after download" in state["last_error"]


def test_existing_file_on_disk_is_adopted_without_downloading(conn, env, settings):
    """Guard 3: an archive built by hand imports for free.

    The file is already at the destination path, so the engine hashes it locally,
    indexes it, and transfers nothing.
    """
    make_fixture_files(env, "alpha", images=1)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")

    # Discover first so the deterministic target path is known, then pre-place the
    # file there and re-run.
    run_sync(conn, env, settings, queue_job(conn, account_id))
    existing = next((env.archive_root / "alpha" / "photos").glob("*.jpg"))
    payload = existing.read_bytes()

    conn.execute("DELETE FROM media_files WHERE account_id = ?", (account_id,))
    conn.execute("UPDATE remote_index SET state = 'pending', attempts = 0 WHERE account_id = ?", (account_id,))
    assert existing.is_file() and existing.read_bytes() == payload

    result = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert result.skipped_existing == 1
    assert result.downloaded == 0
    assert conn.execute("SELECT COUNT(*) FROM media_files WHERE account_id = ?", (account_id,)).fetchone()[0] == 1


def test_remote_advertised_hash_skips_the_transfer(conn, env, settings):
    """Guard 2: a hash the source advertises and we already hold costs no bandwidth."""
    from worker.hashing import sha256_file

    make_fixture_files(env, "alpha", images=1)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    run_sync(conn, env, settings, queue_job(conn, account_id))

    known = sha256_file(next((env.archive_root / "alpha" / "photos").glob("*.jpg")))
    conn.execute(
        """
        INSERT INTO remote_index (account_id, provider, remote_id, remote_url, media_type, remote_hash)
        VALUES (?, 'fixture', 'fx-advertised', 'file:///nowhere.jpg', 'image', ?)
        """,
        (account_id, known),
    )

    result = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert result.skipped_duplicate == 1
    assert result.downloaded == 0
    # The URL does not exist, so a transfer would have failed outright.
    assert result.failed == 0


def test_soft_deleted_media_is_not_redownloaded(conn, env, settings):
    """A file the user deleted stays deleted."""
    make_fixture_files(env, "alpha", images=2)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    run_sync(conn, env, settings, queue_job(conn, account_id))

    victim = conn.execute(
        "SELECT id, rel_path FROM media_files WHERE account_id = ? ORDER BY id LIMIT 1", (account_id,)
    ).fetchone()
    (env.archive_root / "alpha" / victim["rel_path"]).unlink()
    conn.execute("UPDATE media_files SET deleted_at = '2026-02-01T00:00:00Z' WHERE id = ?", (victim["id"],))
    conn.execute("UPDATE remote_index SET state = 'pending', attempts = 0 WHERE account_id = ?", (account_id,))

    result = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert result.downloaded == 0
    assert not (env.archive_root / "alpha" / victim["rel_path"]).exists()


def test_new_account_sync_is_budget_limited_and_requeued(conn, env, settings):
    """A first sync must not pull the whole profile at once."""
    make_fixture_files(env, "alpha", images=30)
    account_id = make_account(conn, "alpha")  # last_success NULL -> brand new

    result = run_sync(conn, env, settings, queue_job(conn, account_id))

    assert result.downloaded == 5  # ceil(30 / 6 ramp runs)
    assert result.remaining == 25
    assert result.requeue_after_seconds is not None
    assert any("deferred to a later run" in note for note in result.notes)


def test_followup_is_queued_once_the_parent_job_closes(conn, env, settings):
    """The remainder becomes a real queued job, so the drip survives a restart.

    It has to be queued *after* the parent finishes: `idx_jobs_one_active` allows
    only one live job per account, which is also what stops a paced backfill from
    quietly spawning parallel workers on the same folder.
    """
    from worker.queue import enqueue_followup, finish_job

    make_fixture_files(env, "alpha", images=30)
    account_id = make_account(conn, "alpha")
    job = queue_job(conn, account_id)

    result = run_sync(conn, env, settings, job)
    assert result.requeue_after_seconds is not None

    # While the parent still holds the slot, a follow-up is correctly refused.
    assert enqueue_followup(conn, account_id, delay_seconds=60, priority=0) is None

    finish_job(conn, int(job["id"]), "succeeded")
    followup_id = enqueue_followup(conn, account_id, delay_seconds=result.requeue_after_seconds, priority=0)
    assert followup_id is not None

    queued = conn.execute(
        "SELECT COUNT(*) FROM scrape_jobs WHERE account_id = ? AND status = 'queued'", (account_id,)
    ).fetchone()[0]
    assert queued == 1


def test_paced_backfill_completes_across_repeated_runs(conn, env, settings):
    """The drip converges, and no run ever re-fetches what an earlier one got.

    Each iteration closes its job the way the worker does, which lets the trigger
    set `last_success_at`. That matters: after the first successful run the account
    is no longer "new", so the ramped budget gives way to the normal per-run cap.
    """
    from worker.queue import finish_job

    make_fixture_files(env, "alpha", images=18)
    account_id = make_account(conn, "alpha")

    downloaded = 0
    runs = 0
    for _ in range(12):
        job = queue_job(conn, account_id)
        result = run_sync(conn, env, settings, job)
        finish_job(conn, int(job["id"]), "succeeded")
        downloaded += result.downloaded
        runs += 1
        if result.message == "Already up to date":
            break

    assert downloaded == 18
    assert runs < 12, "backfill must converge, not stall"
    assert conn.execute("SELECT COUNT(*) FROM media_files WHERE account_id = ?", (account_id,)).fetchone()[0] == 18
    # 18 files downloaded exactly once each: no duplicates on disk.
    assert len(list((env.archive_root / "alpha" / "photos").glob("*.jpg"))) == 18


def test_items_per_run_cap_is_respected(conn, env, settings):
    from worker.config import RuntimeSettings

    make_fixture_files(env, "alpha", images=40)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    conn.execute("UPDATE settings SET value = '7' WHERE key = 'scraper.items_per_run'")

    result = run_sync(conn, env, RuntimeSettings.load(conn), queue_job(conn, account_id))
    assert result.downloaded == 7
    assert result.remaining == 33


def test_account_with_no_links_fails_loudly(conn, env, settings):
    cursor = conn.execute("INSERT INTO accounts (name, archive_path) VALUES ('orphan', 'orphan')")
    account_id = int(cursor.lastrowid)

    result = run_sync(conn, env, settings, queue_job(conn, account_id))
    assert result.status == "failed"

    logged = conn.execute(
        "SELECT event, level FROM event_log WHERE account_id = ? ORDER BY id DESC LIMIT 1", (account_id,)
    ).fetchone()
    assert logged["event"] == "no_links"
    assert logged["level"] == "error"


def test_unavailable_account_is_flagged_and_scraping_disabled(conn, env, settings):
    """A deleted account must stop generating nightly failures."""
    from worker.adapters.base import AccountUnavailableError, ProfileInfo
    from worker.adapters.fixture import FixtureAdapter
    from worker.sync import SyncEngine

    account_id = make_account(conn, "gone", last_success="2026-01-01T00:00:00Z")
    make_fixture_files(env, "gone", images=1)

    class MissingAdapter(FixtureAdapter):
        def probe(self, handle, links):
            return ProfileInfo(handle=handle, exists=False)

    engine = SyncEngine(conn, env, settings, [MissingAdapter()])
    result = engine.run(queue_job(conn, account_id))

    assert result.status == "failed"
    row = conn.execute(
        "SELECT status, platform_state, scrape_enabled FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    assert row["status"] == "flagged"
    assert row["platform_state"] == "unavailable"
    assert row["scrape_enabled"] == 0

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE account_id = ? AND event = 'account_unavailable'",
            (account_id,),
        ).fetchone()[0]
        == 1
    )
    _ = AccountUnavailableError  # imported for the exception contract under test


def test_rate_limit_defers_instead_of_failing(conn, env, settings):
    """A 429 is not the account's fault, so it must not burn a retry attempt."""
    from worker.adapters.base import RateLimitedError
    from worker.adapters.fixture import FixtureAdapter
    from worker.sync import SyncEngine

    account_id = make_account(conn, "busy", last_success="2026-01-01T00:00:00Z")
    make_fixture_files(env, "busy", images=1)

    class ThrottledAdapter(FixtureAdapter):
        def probe(self, handle, links):
            raise RateLimitedError("429 from source", retry_after=120)

    job = queue_job(conn, account_id)
    result = SyncEngine(conn, env, settings, [ThrottledAdapter()]).run(job)

    assert result.status == "deferred"
    assert result.defer_seconds == 120

    row = conn.execute("SELECT status, attempts, message FROM scrape_jobs WHERE id = ?", (job["id"],)).fetchone()
    assert row["status"] == "deferred"
    assert row["attempts"] == 0  # the claim's attempt was handed back
    assert "Rate limited" in row["message"]

    account = conn.execute("SELECT status, scrape_enabled FROM accounts WHERE id = ?", (account_id,)).fetchone()
    assert account["status"] == "active"
    assert account["scrape_enabled"] == 1


def test_fallback_chain_moves_past_a_stub_adapter(conn, env, settings):
    """An unimplemented adapter is skipped, not fatal."""
    from worker.adapters.base import AdapterUnavailableError, ProfileInfo
    from worker.adapters.fixture import FixtureAdapter
    from worker.sync import SyncEngine

    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    make_fixture_files(env, "alpha", images=2)

    class BrokenAdapter:
        name = "broken"
        rank = 1

        def supports(self, handle, links):
            return True

        def probe(self, handle, links):
            raise AdapterUnavailableError("not implemented")

        def list_items(self, handle, links, *, max_items=None, since=None):
            raise AdapterUnavailableError("not implemented")
            yield

        def open_stream(self, item):
            raise AdapterUnavailableError("not implemented")

    result = SyncEngine(conn, env, settings, [BrokenAdapter(), FixtureAdapter()]).run(queue_job(conn, account_id))

    assert result.status == "succeeded"
    assert result.downloaded == 2
    assert result.adapter_used == "fixture"
    _ = ProfileInfo


def test_partial_downloads_are_never_left_in_the_archive(conn, env, settings):
    """A failure mid-transfer must not leave a truncated file posing as media."""
    from worker.adapters.fixture import FixtureAdapter
    from worker.sync import SyncEngine, cleanup_partials

    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    make_fixture_files(env, "alpha", images=2)

    class ExplodingAdapter(FixtureAdapter):
        def open_stream(self, item):
            raise OSError("disk went away mid-transfer")

    SyncEngine(conn, env, settings, [ExplodingAdapter()]).run(queue_job(conn, account_id))

    assert list((env.archive_root / "alpha").rglob("*.jpg")) == []
    assert list((env.archive_root / "alpha").rglob("*.part")) == []
    assert conn.execute("SELECT COUNT(*) FROM media_files WHERE account_id = ?", (account_id,)).fetchone()[0] == 0

    (env.archive_root / "alpha" / "photos" / "orphan.jpg.part").write_bytes(b"leftover")
    assert cleanup_partials(env.archive_root) == 1


def test_dry_run_touches_no_files(conn, env, settings, monkeypatch):
    from worker.config import WorkerEnv

    monkeypatch.setenv("SCRAPER_DRY_RUN", "1")
    dry_env = WorkerEnv()

    make_fixture_files(env, "alpha", images=3)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")

    result = run_sync(conn, dry_env, settings, queue_job(conn, account_id))
    assert result.downloaded == 0
    assert list((env.archive_root / "alpha").rglob("*.jpg")) == []


def test_progress_and_eta_are_published_for_the_ui(conn, env, settings):
    make_fixture_files(env, "alpha", images=4)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    job = queue_job(conn, account_id)

    run_sync(conn, env, settings, job)

    row = conn.execute(
        """
        SELECT items_expected, items_downloaded, pace_delay_ms, eta_seconds, phase, message
          FROM scrape_jobs WHERE id = ?
        """,
        (job["id"],),
    ).fetchone()
    assert row["items_expected"] == 4
    assert row["items_downloaded"] == 4
    assert row["pace_delay_ms"] is not None
    assert row["eta_seconds"] == 0
    assert row["phase"] == "download"


def test_discovery_stops_early_once_history_is_known(conn, env, settings):
    """The early exit is what keeps a routine sync down to a couple of requests."""
    from worker.sync import KNOWN_STREAK_LIMIT

    make_fixture_files(env, "alpha", images=40)
    account_id = make_account(conn, "alpha", last_success="2026-01-01T00:00:00Z")
    run_sync(conn, env, settings, queue_job(conn, account_id))

    second = run_sync(conn, env, settings, queue_job(conn, account_id))
    # Listing stops after a streak of already-known items rather than walking all 40.
    assert second.discovered <= KNOWN_STREAK_LIMIT + 1
