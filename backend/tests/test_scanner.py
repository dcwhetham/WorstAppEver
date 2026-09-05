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

    row = conn.execute("SELECT image_count, video_count, total_bytes FROM accounts WHERE name = 'alpha'").fetchone()
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

    # The loser must point back at the row that kept the hash. A NULL hash with no
    # back reference is indistinguishable from a not-yet-hashed file, which would
    # make the duplicate permanently invisible to the report.
    #
    # The keeper is whichever the walk reached first, and the walk is sorted by
    # path, so it is copy.jpg rather than one.jpg.
    rows = {row["filename"]: row for row in conn.execute("SELECT * FROM media_files")}
    assert rows["copy.jpg"]["content_hash"] is not None
    assert rows["copy.jpg"]["duplicate_of"] is None
    assert rows["one.jpg"]["content_hash"] is None
    assert rows["one.jpg"]["duplicate_of"] == rows["copy.jpg"]["id"]


def test_duplicate_report_surfaces_same_account_copies(conn, env):
    """A duplicate the scan counted has to be one the UI can actually show.

    The copy's hash is NULL by design, so the report has to resolve it through
    `duplicate_of` — otherwise `duplicates_found: 1` is a number the user can
    never act on.
    """
    from app.repositories import media as media_repo
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"same-bytes")
    write_media(env.archive_root, "alpha", "photos", "copy.jpg", b"same-bytes")
    write_media(env.archive_root, "alpha", "photos", "other.jpg", b"different")
    scan_archive(conn=conn, settings=env)

    groups = media_repo.duplicate_report(conn)

    assert len(groups) == 1
    group = groups[0]
    assert group["copies"] == 2
    assert group["accounts"] == 1
    assert group["same_account_copies"] == 1
    assert {m["rel_path"] for m in group["members"]} == {"photos/one.jpg", "photos/copy.jpg"}
    # Exactly one member is the keeper, and it is listed first so the UI can offer
    # "prune the rest" without deciding which to keep itself.
    assert [m["is_duplicate"] for m in group["members"]] == [False, True]
    assert group["members"][0]["rel_path"] == "photos/copy.jpg"  # scanned first


def test_duplicate_report_spans_accounts(conn, env):
    """Cross-account copies group through the same path as same-account ones."""
    from app.repositories import media as media_repo
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "shared.jpg", b"shared-bytes")
    write_media(env.archive_root, "beta", "photos", "shared.jpg", b"shared-bytes")
    report = scan_archive(conn=conn, settings=env)

    # Different accounts, so the unique index never fires: both keep their hash.
    assert report.duplicates_found == 0

    groups = media_repo.duplicate_report(conn)
    assert len(groups) == 1
    assert groups[0]["accounts"] == 2
    assert groups[0]["same_account_copies"] == 0


def test_file_edited_into_a_duplicate_does_not_abort_the_scan(conn, env):
    """An existing file whose contents change to match another is demoted.

    This goes through the UPDATE path rather than the INSERT path, where an
    unhandled IntegrityError would take down the whole scan partway through.
    """
    from app.scanner import scan_archive

    write_media(env.archive_root, "alpha", "photos", "one.jpg", b"first-bytes")
    later = write_media(env.archive_root, "alpha", "photos", "two.jpg", b"second-bytes")
    scan_archive(conn=conn, settings=env)

    later.write_bytes(b"first-bytes")
    report = scan_archive(conn=conn, settings=env)

    assert report.errors == []
    assert report.duplicates_found == 1
    row = conn.execute("SELECT content_hash, duplicate_of FROM media_files WHERE filename='two.jpg'").fetchone()
    assert row["content_hash"] is None
    assert row["duplicate_of"] is not None


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
