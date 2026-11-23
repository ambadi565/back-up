import pytest

from drive_backup.config import Config, WatchedFolder
from drive_backup.drive_client import QuotaExceededError, TransientDriveError
from drive_backup.state import StateDB
from drive_backup.uploader import Outcome, Uploader
from fake_drive import FakeDrive


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "Docs"
    root.mkdir()
    folder = WatchedFolder(str(root), "Docs")
    cfg = Config(folders=[folder], computer_name="host")
    db = StateDB(tmp_path / "s.db")
    drive = FakeDrive()
    up = Uploader(drive, db, sleep=lambda s: None)
    return root, folder, cfg, db, drive, up


def test_new_file_uploaded_to_layout(env):
    root, folder, cfg, db, drive, up = env
    (root / "a").mkdir()
    (root / "a" / "b.txt").write_text("hello")
    assert up.upload(cfg, folder, "a/b.txt") == Outcome.UPLOADED
    f = drive.by_path("Drive Backup/host/Docs/a/b.txt")
    assert f is not None
    assert f["props"]["rel_path"] == "a/b.txt"
    assert f["props"]["folder"] == "Docs"
    assert f["props"]["size"] == "5"
    assert "mtime_ns" in f["props"]
    assert f["revisions"][0]["keep"] is True
    assert db.get_file("Docs", "a/b.txt").drive_id == f["id"]


def test_change_updates_same_id_and_keeps_revision(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    up.upload(cfg, folder, "b.txt")
    first_id = db.get_file("Docs", "b.txt").drive_id
    (root / "b.txt").write_text("two two")
    assert up.upload(cfg, folder, "b.txt") == Outcome.UPLOADED
    f = drive.files[first_id]
    assert [r["keep"] for r in f["revisions"]] == [True, True]
    assert db.get_file("Docs", "b.txt").kept_revisions == 2
    assert len(drive.files) == 1


def test_unchanged_skipped(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    up.upload(cfg, folder, "b.txt")
    assert up.upload(cfg, folder, "b.txt") == Outcome.UNCHANGED


def test_vanished(env):
    root, folder, cfg, db, drive, up = env
    assert up.upload(cfg, folder, "nope.txt") == Outcome.VANISHED


def test_changed_during_upload_requeues(env):
    root, folder, cfg, db, drive, up = env
    target = root / "b.txt"
    target.write_text("one")
    real = drive.create_file

    def create_and_modify(*a, **kw):
        res = real(*a, **kw)
        target.write_text("something much longer")
        return res
    drive.create_file = create_and_modify
    assert up.upload(cfg, folder, "b.txt") == Outcome.CHANGED_DURING_UPLOAD
    assert db.get_file("Docs", "b.txt") is None
    assert db.queue_size() == 1


def test_recreates_when_remote_missing(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    up.upload(cfg, folder, "b.txt")
    drive.delete_remote(db.get_file("Docs", "b.txt").drive_id)
    (root / "b.txt").write_text("two two")
    assert up.upload(cfg, folder, "b.txt") == Outcome.UPLOADED
    assert drive.by_path("Drive Backup/host/Docs/b.txt")["revisions"][0]["content"] == b"two two"


def test_transient_retried_then_succeeds(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    drive.fail_next = [TransientDriveError("x"), TransientDriveError("y")]
    assert up.upload(cfg, folder, "b.txt") == Outcome.UPLOADED


def test_transient_gives_up_after_retries(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    drive.fail_next = [TransientDriveError(str(i)) for i in range(10)]
    with pytest.raises(TransientDriveError):
        up.upload(cfg, folder, "b.txt")


def test_quota_propagates(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    drive.quota_limit = 1
    with pytest.raises(QuotaExceededError):
        up.upload(cfg, folder, "b.txt")


def test_revision_limit_warns(env):
    root, folder, cfg, db, drive, up = env
    drive.max_kept = 1
    (root / "b.txt").write_text("one")
    up.upload(cfg, folder, "b.txt")
    (root / "b.txt").write_text("two two")
    assert up.upload(cfg, folder, "b.txt") == Outcome.UPLOADED
    assert "revlimit:Docs/b.txt" in db.warnings()


def test_forced_upload_warns_and_normal_clears(env):
    root, folder, cfg, db, drive, up = env
    (root / "b.txt").write_text("one")
    up.upload(cfg, folder, "b.txt", forced=True)
    assert "unsettled:Docs/b.txt" in db.warnings()
    (root / "b.txt").write_text("two two")
    up.upload(cfg, folder, "b.txt")
    assert "unsettled:Docs/b.txt" not in db.warnings()


def test_folder_ids_cached(env):
    root, folder, cfg, db, drive, up = env
    (root / "a.txt").write_text("1")
    (root / "b.txt").write_text("2")
    up.upload(cfg, folder, "a.txt")
    n = drive.calls.count("find_folder")
    up.upload(cfg, folder, "b.txt")
    assert drive.calls.count("find_folder") == n


def test_concurrent_uploads_create_folders_once(env):
    import threading
    import time as _t
    root, folder, cfg, db, drive, up = env
    real_find = drive.find_folder

    def slow_find(*a):
        r = real_find(*a)
        _t.sleep(0.05)
        return r
    drive.find_folder = slow_find
    (root / "a.txt").write_text("1")
    (root / "b.txt").write_text("2")
    ts = [threading.Thread(target=up.upload, args=(cfg, folder, n)) for n in ("a.txt", "b.txt")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    names = [f["name"] for f in drive.folders.values()]
    assert names.count("Drive Backup") == 1
