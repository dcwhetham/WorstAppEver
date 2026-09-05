"""API surface tests: cards, toggles, links, jobs, bundling, raw media, logs."""

from __future__ import annotations

import io
import zipfile

from conftest import write_media


def test_health_reports_archive_and_migrations(client, env):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["migrations_applied"] >= 3
    assert body["archive_root_present"] is True


def test_create_account_derives_links_and_folders(client, env):
    created = client.post("/api/accounts", json={"name": "@aurora.films", "is_favorite": True}).json()

    # The leading @ is stripped, because the name is also a folder name.
    assert created["name"] == "aurora.films"
    assert created["is_favorite"] is True

    providers = {link["provider"] for link in created["links"]}
    assert {"instagram", "imginn", "pixnoy"} <= providers

    assert (env.archive_root / "aurora.films" / "photos").is_dir()
    assert (env.archive_root / "aurora.films" / "videos").is_dir()


def test_create_account_rejects_unsafe_names(client):
    assert client.post("/api/accounts", json={"name": "../../etc/passwd"}).status_code == 422
    assert client.post("/api/accounts", json={"name": "has spaces"}).status_code == 422


def test_duplicate_account_is_conflict(client):
    client.post("/api/accounts", json={"name": "alpha"})
    assert client.post("/api/accounts", json={"name": "alpha"}).status_code == 409


def test_new_account_gets_a_bootstrap_job(client):
    account = client.post("/api/accounts", json={"name": "alpha"}).json()
    jobs = client.get("/api/jobs", params={"account_id": account["id"]}).json()
    assert [job["trigger"] for job in jobs] == ["bootstrap"]


def test_filters_and_favorite_ordering(client):
    client.post("/api/accounts", json={"name": "zulu"})
    client.post("/api/accounts", json={"name": "alpha", "is_favorite": True})
    client.post("/api/accounts", json={"name": "legacy.one", "status": "legacy"})

    names = [card["name"] for card in client.get("/api/accounts").json()]
    # Favourites float to the top regardless of the requested sort.
    assert names[0] == "alpha"

    favorites = client.get("/api/accounts", params={"favorite": True}).json()
    assert [card["name"] for card in favorites] == ["alpha"]

    legacy = client.get("/api/accounts", params={"status": "legacy"}).json()
    assert [card["name"] for card in legacy] == ["legacy.one"]

    searched = client.get("/api/accounts", params={"q": "ZUL"}).json()
    assert [card["name"] for card in searched] == ["zulu"]

    response = client.get("/api/accounts")
    assert response.headers["x-total-count"] == "3"


def test_toggles_flip_without_a_body(client):
    account = client.post("/api/accounts", json={"name": "alpha"}).json()
    account_id = account["id"]

    assert client.post(f"/api/accounts/{account_id}/favorite").json()["is_favorite"] is True
    assert client.post(f"/api/accounts/{account_id}/favorite").json()["is_favorite"] is False
    assert client.post(f"/api/accounts/{account_id}/scrape-toggle").json()["scrape_enabled"] is False
    assert (
        client.post(f"/api/accounts/{account_id}/scrape-toggle", json={"value": True}).json()[
            "scrape_enabled"
        ]
        is True
    )


def test_run_now_collapses_duplicate_clicks(client):
    account = client.post("/api/accounts", json={"name": "alpha", "scrape_enabled": False}).json()
    first = client.post(f"/api/accounts/{account['id']}/run").json()
    second = client.post(f"/api/accounts/{account['id']}/run").json()

    assert first["created"] is True
    # Mashing the button must not stack overlapping syncs.
    assert second["created"] is False
    assert second["job_id"] == first["job_id"]


def test_manual_links_can_be_added_and_removed(client):
    account = client.post("/api/accounts", json={"name": "alpha"}).json()
    account_id = account["id"]

    created = client.post(
        f"/api/accounts/{account_id}/links",
        json={"url": "https://imginn.com/alpha.backup/", "label": "Backup"},
    ).json()
    assert created["kind"] == "manual"
    # Provider is inferred from the host rather than being required.
    assert created["provider"] == "imginn"

    assert (
        client.post(
            f"/api/accounts/{account_id}/links", json={"url": "https://imginn.com/alpha.backup/"}
        ).status_code
        == 409
    )

    assert client.delete(f"/api/accounts/{account_id}/links/{created['id']}").status_code == 204
    remaining = {link["id"] for link in client.get(f"/api/accounts/{account_id}/links").json()}
    assert created["id"] not in remaining


def test_media_listing_and_raw_streaming(client, env):
    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"a" * 512)
    write_media(env.archive_root, "alpha", "videos", "clip.mp4", b"v" * 2048)
    client.post("/api/scan/sync", json={})

    account = client.get("/api/accounts").json()[0]
    page = client.get(f"/api/accounts/{account['id']}/media").json()
    assert page["total"] == 2

    images = client.get(f"/api/accounts/{account['id']}/media", params={"media_type": "image"}).json()
    assert images["total"] == 1

    media_id = images["items"][0]["id"]
    full = client.get(f"/api/media/{media_id}/raw")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert full.content == b"a" * 512


def test_range_requests_enable_video_seeking(client, env):
    """206 responses are what make the `<video>` scrubber work."""
    write_media(env.archive_root, "alpha", "videos", "clip.mp4", bytes(range(256)) * 8)
    client.post("/api/scan/sync", json={})

    account = client.get("/api/accounts").json()[0]
    media_id = client.get(f"/api/accounts/{account['id']}/media").json()["items"][0]["id"]

    partial = client.get(f"/api/media/{media_id}/raw", headers={"Range": "bytes=10-19"})
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 10-19/2048"
    assert partial.content == bytes(range(10, 20))

    suffix = client.get(f"/api/media/{media_id}/raw", headers={"Range": "bytes=-8"})
    assert suffix.status_code == 206
    assert len(suffix.content) == 8

    assert client.get(f"/api/media/{media_id}/raw", headers={"Range": "bytes=99999-"}).status_code == 416


def test_missing_file_returns_410_and_flags_the_row(client, env):
    path = write_media(env.archive_root, "alpha", "photos", "one.jpg", b"a" * 512)
    client.post("/api/scan/sync", json={})
    account = client.get("/api/accounts").json()[0]
    media_id = client.get(f"/api/accounts/{account['id']}/media").json()["items"][0]["id"]

    path.unlink()
    assert client.get(f"/api/media/{media_id}/raw").status_code == 410

    listed = client.get(f"/api/accounts/{account['id']}/media").json()
    assert listed["total"] == 0


def test_bundle_produces_a_stored_zip_of_raw_files(client, env):
    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"a" * 600)
    write_media(env.archive_root, "alpha", "videos", "clip.mp4", b"v" * 900)
    client.post("/api/scan/sync", json={})
    account = client.get("/api/accounts").json()[0]

    response = client.get(f"/api/accounts/{account['id']}/bundle")
    assert response.status_code == 200
    assert response.headers["x-bundle-file-count"] == "2"
    assert response.headers["x-bundle-cached"] == "0"

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        assert names == ["alpha/photos/one.jpg", "alpha/videos/clip.mp4"]
        # Stored, not deflated: the payload is already compressed media.
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        assert archive.read("alpha/photos/one.jpg") == b"a" * 600

    # Unchanged inventory hits the content-addressed cache.
    assert client.get(f"/api/accounts/{account['id']}/bundle").headers["x-bundle-cached"] == "1"


def test_bundle_of_empty_account_is_404(client):
    account = client.post("/api/accounts", json={"name": "alpha"}).json()
    assert client.get(f"/api/accounts/{account['id']}/bundle").status_code == 404


def test_batch_run_staggers_and_batch_update_applies(client):
    ids = [
        client.post("/api/accounts", json={"name": f"acct{i}", "scrape_enabled": False}).json()["id"]
        for i in range(3)
    ]

    result = client.post("/api/batch/run", json={"account_ids": ids}).json()
    assert result["count"] == 3
    scheduled = [job["scheduled_for"] for job in client.get("/api/jobs").json() if job["trigger"] == "batch"]
    # Staggered rather than simultaneous, so a bulk scrape is not a traffic spike.
    assert len(set(scheduled)) == 3

    updated = client.patch(
        "/api/batch/accounts", json={"account_ids": ids, "patch": {"is_favorite": True}}
    ).json()
    assert updated["updated"] == 3
    assert all(card["is_favorite"] for card in client.get("/api/accounts").json())


def test_logs_expose_scanner_and_account_events(client, env):
    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"a" * 512)
    client.post("/api/scan/sync", json={})
    account = client.get("/api/accounts").json()[0]

    events = {entry["event"] for entry in client.get(f"/api/accounts/{account['id']}/logs").json()}
    assert {"account_discovered", "scan_complete"} <= events

    scan_entry = next(
        e
        for e in client.get("/api/logs", params={"source": "scanner"}).json()
        if e["event"] == "scan_complete"
    )
    assert scan_entry["detail"]["files_added"] == 1


def test_log_level_filter_is_a_floor(client, conn):
    from app.logs import log_event

    account_id = client.post("/api/accounts", json={"name": "alpha"}).json()["id"]
    log_event(
        conn, level="warn", source="scraper", event="rate_limited", message="429", account_id=account_id
    )
    log_event(conn, level="error", source="scraper", event="blocked", message="403", account_id=account_id)
    log_event(conn, level="debug", source="scraper", event="noise", message="chatter", account_id=account_id)

    warn_and_up = {e["event"] for e in client.get("/api/logs", params={"level": "warn"}).json()}
    assert {"rate_limited", "blocked"} <= warn_and_up
    assert "noise" not in warn_and_up


def test_repeated_errors_coalesce_into_one_row(conn, client):
    from app.logs import log_event

    account_id = client.post("/api/accounts", json={"name": "alpha"}).json()["id"]
    for attempt in range(5):
        log_event(
            conn,
            level="warn",
            source="scraper",
            event="rate_limited",
            message=f"429 from source, retry after {attempt * 10}s",
            account_id=account_id,
        )

    rows = conn.execute("SELECT occurrences FROM event_log WHERE event = 'rate_limited'").fetchall()
    # Volatile numbers are masked out of the fingerprint, so all five collapse.
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 5


def test_successful_job_clears_the_error_badge(client, conn):
    from app.logs import log_event

    account_id = client.post("/api/accounts", json={"name": "alpha", "scrape_enabled": False}).json()["id"]
    log_event(
        conn, level="error", source="scraper", event="adapter_failed", message="boom", account_id=account_id
    )
    assert client.get(f"/api/accounts/{account_id}").json()["unresolved_error_count"] == 1

    job_id = client.post(f"/api/accounts/{account_id}/run").json()["job_id"]
    conn.execute("UPDATE scrape_jobs SET status = 'succeeded' WHERE id = ?", (job_id,))

    card = client.get(f"/api/accounts/{account_id}").json()
    assert card["unresolved_error_count"] == 0
    assert card["last_error"] is None


def test_settings_reject_unknown_keys(client):
    assert client.patch("/api/settings", json={"scraper.min_delay_ms": 2500}).status_code == 200
    assert client.get("/api/settings").json()["scraper.min_delay_ms"]["value"] == 2500
    assert client.patch("/api/settings", json={"scraper.no_such_key": 1}).status_code == 400


def test_stats_summarise_the_archive(client, env):
    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"a" * 512)
    client.post("/api/scan/sync", json={})
    client.post("/api/accounts", json={"name": "legacy.one", "status": "legacy"})

    stats = client.get("/api/stats").json()
    assert stats["account_count"] == 2
    assert stats["image_count"] == 1
    assert stats["legacy_count"] == 1


def test_deleting_an_account_never_touches_the_files(client, env):
    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"a" * 512)
    client.post("/api/scan/sync", json={})
    account = client.get("/api/accounts").json()[0]

    assert client.delete(f"/api/accounts/{account['id']}").status_code == 204
    assert client.get("/api/accounts").json() == []
    assert (env.archive_root / "alpha" / "photos" / "one.jpg").is_file()
