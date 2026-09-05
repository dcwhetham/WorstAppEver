"""Scanner behaviour: import, dedup, missing detection, incremental rescan."""

from __future__ import annotations

from pathlib import Path

from conftest import write_media


def test_scan_discovers_accounts_and_classifies_media(conn, env):
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"image-one")
    write_media(env.archive_root, "alpha", "photos", "two.png", b"image-two")
    write_media(env.archive_root, "alpha", "videos", "clip.mp4", b"video-bytes")
    write_media(env.archive_root, "beta", "photos", "solo.jpg", b"beta-image")

    report = scan_archive(conn=conn, settings=env)

    assert report.accounts_created == 2
    assert report.files_added == 4

    row = conn.execute(
        "SELECT image_count, video_count, total_bytes FROM accounts WHERE name = 'alpha'"
    ).fetchone()
    assert (row["image_count"], row["video_count"]) == (2, 1)
    assert row["total_bytes"] == len(b"image-one") + len(b"image-two") + len(b"video-bytes")


def test_rescan_is_incremental(conn, env):
    """An unchanged file must not be re-hashed. This is what keeps a rescan of a
    large archive a seconds-long operation instead of a full re-read."""
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"image-one")
    scan_archive(conn=conn, settings=env)

    second = scan_archive(conn=conn, settings=env)
    assert second.files_seen == 1
    assert second.files_rehashed == 0
    assert second.files_added == 0


def test_changed_file_is_rehashed(conn, env):
    from app.scanner import scan_archive

    path = write_media(env.archive_root, "alpha", "photos", "one.jpg", b"original")
    scan_archive(conn=conn, settings=env)
    before = conn.execute("SELECT content_hash FROM media_files").fetchone()["content_hash"]

    path.write_bytes(b"replaced-with-different-content")
    report = scan_archive(conn=conn, settings=env)

    assert report.files_rehashed == 1
    after = conn.execute("SELECT content_hash FROM media_files").fetchone()["content_hash"]
    assert after != before


def test_identical_files_do_not_both_claim_the_dedup_slot(conn, env):
    """Two files with identical bytes stay visible, but only one holds the hash.

    The partial unique index makes that a database guarantee; the scanner's job is
    to degrade gracefully rather than drop the second file from the index.
    """
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"same-bytes")
    write_media(env.archive_root, "alpha", "photos", "copy.jpg", b"same-bytes")

    report = scan_archive(conn=conn, settings=env)

    assert report.duplicates_found == 1
    assert conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0] == 2
    hashed = conn.execute("SELECT COUNT(*) FROM media_files WHERE content_hash IS NOT NULL").fetchone()[0]
    assert hashed == 1


def test_missing_file_is_tombstoned_not_deleted(conn, env):
    """A deleted file keeps its row so the scraper will not re-download it."""
    from app.scanner import scan_archive

    path = write_media(env.archive_root, "alpha", "photos", "one.jpg", b"image-one")
    write_media(env.archive_root, "alpha", "photos", "two.jpg", b"image-two")
    scan_archive(conn=conn, settings=env)

    path.unlink()
    report = scan_archive(conn=conn, settings=env)

    assert report.files_marked_missing == 1
    assert conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0] == 2
    assert conn.execute("SELECT image_count FROM accounts WHERE name='alpha'").fetchone()[0] == 2

    row = conn.execute("SELECT is_missing FROM media_files WHERE filename = 'one.jpg'").fetchone()
    assert row["is_missing"] == 1


def test_restored_file_clears_the_tombstone(conn, env):
    from app.scanner import scan_archive

    path = write_media(env.archive_root, "alpha", "photos", "one.jpg", b"image-one")
    scan_archive(conn=conn, settings=env)
    payload = path.read_bytes()
    path.unlink()
    scan_archive(conn=conn, settings=env)

    path.write_bytes(payload)
    report = scan_archive(conn=conn, settings=env)

    assert report.files_restored + report.files_updated >= 1
    assert conn.execute("SELECT is_missing FROM media_files").fetchone()["is_missing"] == 0


def test_junk_files_are_ignored(conn, env):
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "real.jpg", b"image-one")
    write_media(env.archive_root, "alpha", "photos", ".DS_Store", b"junk-bytes")
    write_media(env.archive_root, "alpha", "photos", "half.jpg.part", b"partial-download")
    write_media(env.archive_root, "alpha", "photos", "meta.json", b"{}")

    report = scan_archive(conn=conn, settings=env)
    assert report.files_added == 1


def test_safe_join_rejects_traversal(env):
    import pytest

    from app.util import safe_join

    with pytest.raises(ValueError):
        safe_join(env.archive_root, "..", "..", "etc", "passwd")

    assert safe_join(env.archive_root, "alpha", "photos") == Path(env.archive_root / "alpha" / "photos")
