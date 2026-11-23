import shutil

import pytest

from drive_backup.config import add_folder, load_config, save_config
from drive_backup.drive_client import (
    AuthError, DriveError, QuotaExceededError, TransientDriveError,
)
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
                  sleep=lambda s: None, wall=clock, synchronous=True)
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
    for _ in range(200):
        drive.fail_next = [TransientDriveError("down")] * 6
        svc.tick()
        if svc.db.get_status().get("state") == "failing":
            break
        clock.t += 61
    assert svc.db.get_status()["state"] == "failing"
    assert "failing" in svc.db.warnings()
    assert notes == ["Drive Backup: uploads failing"]
    drive.fail_next = []  # connection is back
    clock.t += 3601
    svc.tick()
    assert remote(drive, "a.txt") is not None
    assert "failing" not in svc.db.warnings()


def test_permanent_error_does_not_block_queue(env):
    root, _, drive, clock, notes, svc = env
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    real = drive.create_file
    bad = {"on": True}

    def create(parent, name, path, props):
        if name == "a.txt" and bad["on"]:
            raise DriveError("HTTP 400 badRequest")
        return real(parent, name, path, props)
    drive.create_file = create
    svc.full_scan()
    for _ in range(3):
        svc.tick()
        clock.t += 1
    assert remote(drive, "b.txt") is not None
    assert remote(drive, "a.txt") is None
    assert "error:Docs/a.txt" in svc.db.warnings()
    assert svc.db.queue_size() == 1
    bad["on"] = False
    clock.t += 3601
    svc.tick()
    assert remote(drive, "a.txt") is not None
    assert "error:Docs/a.txt" not in svc.db.warnings()


def test_tick_does_not_block_on_slow_upload(env, tmp_path):
    import threading
    root, cfg_path, drive, clock, notes, svc = env
    svc._sync = False
    gate = threading.Event()
    real = drive.create_file

    def slow_create(*a):
        gate.wait(5)
        return real(*a)
    drive.create_file = slow_create
    (root / "big.bin").write_bytes(b"x" * 100)
    svc.full_scan()
    svc.tick()                       # starts the upload in the background, returns
    cfg = load_config(cfg_path)
    cfg.paused = True
    save_config(cfg, cfg_path)
    svc.reload_config_if_changed(force=True)
    svc.tick()
    assert svc.cfg.paused is True    # config handled while the upload is running
    assert remote(drive, "big.bin") is None
    gate.set()
    svc.wait_idle(5)
    svc.tick()
    assert remote(drive, "big.bin") is not None
    assert svc.db.queue_size() == 0
    svc.shutdown()


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


def test_rebuild_runs_once_signed_in_after_start(env, tmp_path):
    from drive_backup.uploader import Uploader
    root, cfg_path, drive, clock, notes, svc = env
    (root / "a.txt").write_text("hi")
    cfg = load_config(cfg_path)
    # an existing backup from before the reinstall
    Uploader(drive, StateDB(tmp_path / "old.db"), sleep=lambda s: None).upload(
        cfg, cfg.folders[0], "a.txt")
    assert len(drive.files) == 1
    # service starts before sign-in
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthError("not signed in")
        return drive
    svc2 = Service(cfg_path, StateDB(tmp_path / "fresh.db"), factory, clock=clock,
                   notifier=lambda t, b: None, token_path=tmp_path / "token.json",
                   sleep=lambda s: None, wall=clock, synchronous=True)
    svc2.full_scan()
    svc2.tick()
    assert svc2.db.get_status()["state"] == "signin_needed"
    (tmp_path / "token.json").write_text("{}")
    svc2.tick()
    assert len(drive.files) == 1          # no duplicate upload
    assert svc2.db.get_file("Docs", "a.txt") is not None
