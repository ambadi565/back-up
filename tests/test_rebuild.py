from drive_backup.config import Config, WatchedFolder
from drive_backup.rebuild import rebuild_from_drive
from drive_backup.scanner import scan_folder
from drive_backup.state import StateDB
from drive_backup.uploader import Outcome, Uploader
from fake_drive import FakeDrive


def test_reinstall_reconnects_to_existing_backup(tmp_path):
    root = tmp_path / "Docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a")
    (root / "sub" / "b.txt").write_text("b")
    folder = WatchedFolder(str(root), "Docs")
    cfg = Config(folders=[folder], computer_name="host")
    drive = FakeDrive()
    old = Uploader(drive, StateDB(tmp_path / "old.db"), sleep=lambda s: None)
    old.upload(cfg, folder, "a.txt")
    old.upload(cfg, folder, "sub/b.txt")
    a_id = drive.by_path("Drive Backup/host/Docs/a.txt")["id"]

    fresh = StateDB(tmp_path / "new.db")
    assert rebuild_from_drive(drive, fresh, cfg) == 2
    assert scan_folder(folder, fresh, []) == []

    (root / "a.txt").write_text("changed a")
    assert scan_folder(folder, fresh, []) == ["a.txt"]
    up = Uploader(drive, fresh, sleep=lambda s: None)
    assert up.upload(cfg, folder, "a.txt") == Outcome.UPLOADED
    assert db_id(fresh) == a_id
    assert len(drive.files) == 2


def db_id(db):
    return db.get_file("Docs", "a.txt").drive_id


def test_no_remote_tree_creates_nothing(tmp_path):
    drive = FakeDrive()
    cfg = Config(folders=[WatchedFolder(str(tmp_path), "Docs")], computer_name="host")
    assert rebuild_from_drive(drive, StateDB(tmp_path / "s.db"), cfg) == 0
    assert drive.folders == {}


def test_skips_when_db_not_empty(tmp_path):
    root = tmp_path / "Docs"
    root.mkdir()
    (root / "a.txt").write_text("a")
    folder = WatchedFolder(str(root), "Docs")
    cfg = Config(folders=[folder], computer_name="host")
    drive = FakeDrive()
    db = StateDB(tmp_path / "s.db")
    Uploader(drive, db, sleep=lambda s: None).upload(cfg, folder, "a.txt")
    assert rebuild_from_drive(drive, db, cfg) == 0
