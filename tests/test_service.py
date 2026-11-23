import shutil

import pytest

from drive_backup.config import add_folder, load_config, save_config
from drive_backup.drive_client import AuthError, QuotaExceededError, TransientDriveError
from drive_backup.service import Service
from drive_backup.state import StateDB
from fake_drive import FakeDrive


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "Docs"
    root.mkdir()
    cfg_path = tmp_path / "config.json"
    cfg = add_folder(load_config(cfg_path), str(root))
    cfg.computer_name = "host"
    save_config(cfg, cfg_path)
    drive = FakeDrive()
    clock = Clock()
    notes = []
    svc = Service(cfg_path, StateDB(tmp_path / "s.db"), lambda: drive, clock=clock,
                  notifier=lambda t, b: notes.append(t), token_path=tmp_path / "token.json",
                  sleep=lambda s: None)
    return root, cfg_path, drive, clock, notes, svc


def remote(drive, rel):
    return drive.by_path(f"Drive Backup/host/Docs/{rel}")


def test_event_settles_then_uploads(env):
    root, _, drive, clock, _, svc = env
    (root / "a.txt").write_text("hi")
    svc.on_fs_event(str(root / "a.txt"))
    clock.t += 1
    svc.tick()
    assert remote(drive, "a.txt") is None
    clock.t += 5
    svc.tick()
    assert remote(drive, "a.txt") is not None
    assert svc.db.get_status()["state"] == "ok"


def test_ignored_event_not_uploaded(env):
    root, _, drive, clock, _, svc = env
    (root / "x.tmp").write_text("hi")
    svc.on_fs_event(str(root / "x.tmp"))
    clock.t += 10
    svc.tick()
    assert drive.files == {}


def test_paused_queues_but_does_not_upload(env):
    root, cfg_path, drive, clock, _, svc = env
    cfg = load_config(cfg_path)
    cfg.paused = True
    save_config(cfg, cfg_path)
    svc.reload_config_if_changed(force=True)
    (root / "a.txt").write_text("hi")
    svc.full_scan()
    svc.tick()
    assert drive.files == {}
    assert svc.db.queue_size() == 1
    assert svc.db.get_status()["state"] == "paused"


def test_quota_sets_drive_full_and_keeps_queue(env):
    root, _, drive, clock, notes, svc = env
    drive.quota_limit = 1
    (root / "a.txt").write_text("hi")
    svc.full_scan()
    svc.tick()
    assert svc.db.get_status()["state"] == "drive_full"
    assert svc.db.queue_size() == 1
    assert len(notes) == 1
    svc.tick()
    assert len(notes) == 1  # notified once
    drive.quota_limit = None
    clock.t += 601
    svc.tick()
    assert remote(drive, "a.txt") is not None
    assert svc.db.get_status()["state"] == "ok"


def test_auth_error_sets_signin_needed(env, tmp_path):
    root, _, drive, clock, notes, svc = env
    drive.fail_next = [AuthError("expired")]
    (root / "a.txt").write_text("hi")
    svc.full_scan()
    svc.tick()
    assert svc.db.get_status()["state"] == "signin_needed"
    assert svc.db.queue_size() == 1
    assert notes
    clock.t += 10_000
    svc.tick()
    assert drive.files == {}  # still waiting for a new token
    (tmp_path / "token.json").write_text("{}")
    svc.tick()
    assert remote(drive, "a.txt") is not None


def test_persistent_transient_failure_warns_after_an_hour(env):
    root, _, drive, clock, notes, svc = env
    (root / "a.txt").write_text("hi")
    svc.full_scan()
    for _ in range(62):
        drive.fail_next = [TransientDriveError("down")] * 6
        svc.tick()
        clock.t += 61
    assert svc.db.get_status()["state"] == "failing"
    assert "failing" in svc.db.warnings()
    clock.t += 61
    svc.tick()
    assert remote(drive, "a.txt") is not None
    assert "failing" not in svc.db.warnings()


def test_missing_folder_warns(env):
    root, _, drive, clock, _, svc = env
    (root / "a.txt").write_text("hi")
    svc.full_scan()
    svc.tick()
    assert remote(drive, "a.txt") is not None
    shutil.rmtree(root)
    svc.full_scan()
    assert "missing:Docs" in svc.db.warnings()
    assert svc.db.get_file("Docs", "a.txt") is not None
    root.mkdir()
    svc.full_scan()
    assert "missing:Docs" not in svc.db.warnings()


def test_startup_scan_catches_offline_changes(env):
    root, _, drive, clock, _, svc = env
    (root / "a.txt").write_text("v1")
    svc.full_scan()
    svc.tick()
    (root / "a.txt").write_text("version two")
    svc.full_scan()
    svc.tick()
    assert remote(drive, "a.txt")["revisions"][-1]["content"] == b"version two"


def test_local_delete_does_nothing_remote(env):
    root, _, drive, clock, _, svc = env
    (root / "a.txt").write_text("hi")
    svc.full_scan()
    svc.tick()
    (root / "a.txt").unlink()
    svc.full_scan()
    svc.tick()
    assert remote(drive, "a.txt") is not None


def test_new_folder_in_config_is_scanned(env, tmp_path):
    root, cfg_path, drive, clock, _, svc = env
    other = tmp_path / "Photos"
    other.mkdir()
    (other / "p.jpg").write_bytes(b"x")
    save_config(add_folder(load_config(cfg_path), str(other)), cfg_path)
    svc.reload_config_if_changed(force=True)
    svc.tick()
    assert drive.by_path("Drive Backup/host/Photos/p.jpg") is not None


def test_directory_event_triggers_scan(env):
    root, _, drive, clock, _, svc = env
    (root / "moved").mkdir()
    (root / "moved" / "f.txt").write_text("x")
    svc.on_fs_event(str(root / "moved"))
    svc.tick()
    assert remote(drive, "moved/f.txt") is not None
