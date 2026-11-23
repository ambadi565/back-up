"""The background service: watch folders, settle changes, upload from a persistent queue."""
from __future__ import annotations

import logging
import logging.handlers
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import notify as notify_mod
from .config import Config, WatchedFolder, load_config
from .drive_client import AuthError, DriveClient, QuotaExceededError, TransientDriveError
from .ignore import is_ignored
from .rebuild import rebuild_from_drive
from .scanner import scan_folder, to_rel
from .settle import Settler
from .state import StateDB
from .uploader import Outcome, Uploader

log = logging.getLogger(__name__)

SCAN_INTERVAL_S = 2700
CONFIG_POLL_S = 5
QUOTA_REFRESH_S = 600
QUOTA_BACKOFF_S = 600
TRANSIENT_BACKOFF_S = 60
FAILING_AFTER_S = 3600
WORKERS = 2


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Service:
    def __init__(self, config_path: Path, db: StateDB, drive_factory: Callable[[], DriveClient],
                 clock: Callable[[], float] = time.monotonic,
                 notifier: Callable[[str, str], None] = notify_mod.notify,
                 token_path: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.config_path = Path(config_path)
        self.db = db
        self._drive_factory = drive_factory
        self._clock = clock
        self._notify = notifier
        self._token_path = Path(token_path) if token_path else None
        self._sleep = sleep
        self.settler = Settler(clock=clock)
        self.cfg: Config = load_config(self.config_path)
        self._cfg_mtime = self._mtime(self.config_path)
        self._drive: DriveClient | None = None
        self._uploader: Uploader | None = None
        self._backoff_until = 0.0
        self._waiting_for_signin = False
        self._auth_token_mtime: float | None = None
        self._notified: set[str] = set()
        self._failing_since: float | None = None
        self._last_quota = -1e18
        self._scan_requested: set[str] = set()
        self._rebuild_checked = False
        self._scan_lock = threading.Lock()
        self.on_folders_changed: Callable[[], None] | None = None

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _mtime(p: Path | None) -> float | None:
        try:
            return os.stat(p).st_mtime_ns if p else None
        except OSError:
            return None

    def _folder(self, drive_name: str) -> WatchedFolder | None:
        return next((f for f in self.cfg.folders if f.drive_name == drive_name), None)

    def _notify_once(self, key: str, title: str, body: str) -> None:
        if key not in self._notified:
            self._notified.add(key)
            self._notify(title, body)

    def _get_uploader(self) -> Uploader:
        if self._uploader is None:
            self._drive = self._drive_factory()
            self._uploader = Uploader(self._drive, self.db, sleep=self._sleep)
        return self._uploader

    # -- config ----------------------------------------------------------------
    def reload_config_if_changed(self, force: bool = False) -> None:
        m = self._mtime(self.config_path)
        if not force and m == self._cfg_mtime:
            return
        self._cfg_mtime = m
        old = {f.drive_name for f in self.cfg.folders}
        self.cfg = load_config(self.config_path)
        new = [f for f in self.cfg.folders if f.drive_name not in old]
        for f in new:
            self._scan_one(f)
        if {f.drive_name for f in self.cfg.folders} != old and self.on_folders_changed:
            self.on_folders_changed()
        log.info("config reloaded (%d folders, paused=%s)", len(self.cfg.folders), self.cfg.paused)

    # -- detection -------------------------------------------------------------
    def _scan_one(self, folder: WatchedFolder) -> None:
        changed = scan_folder(folder, self.db, self.cfg.ignore)
        if changed is None:
            self.db.add_warning(f"missing:{folder.drive_name}", f"Folder {folder.path} not found")
            return
        self.db.clear_warning(f"missing:{folder.drive_name}")
        for rel in changed:
            self.db.enqueue(folder.drive_name, rel)

    def full_scan(self) -> None:
        for f in self.cfg.folders:
            self._scan_one(f)
        self.db.set_status("last_scan", _now_iso())

    def on_fs_event(self, abs_path: str) -> None:
        try:
            p = Path(abs_path)
            for f in self.cfg.folders:
                root = Path(f.path)
                if p != root and root not in p.parents:
                    continue
                if p.is_symlink():
                    return
                rel = to_rel(f.path, abs_path)
                if is_ignored(rel, self.cfg.ignore):
                    return
                if p.is_dir():
                    # e.g. a folder moved in: its files produce no events of their own
                    with self._scan_lock:
                        self._scan_requested.add(f.drive_name)
                    return
                self.settler.touch((f.drive_name, rel))
                return
        except Exception:
            log.exception("error handling event for %s", abs_path)

    # -- main step -------------------------------------------------------------
    def tick(self) -> None:
        with self._scan_lock:
            requested, self._scan_requested = self._scan_requested, set()
        for name in requested:
            f = self._folder(name)
            if f:
                self._scan_one(f)
        for (name, rel), forced in self.settler.ready():
            self.db.enqueue(name, rel, forced=forced)

        self.db.set_status("queue", str(self.db.queue_size()))
        self.db.set_status("pending", str(self.settler.pending_count()))
        self.db.set_status("heartbeat", _now_iso())

        if self.cfg.paused:
            self.db.set_status("state", "paused")
            return
        now = self._clock()
        if self._waiting_for_signin:
            if self._mtime(self._token_path) == self._auth_token_mtime:
                return
            self._waiting_for_signin = False
            self._uploader = None
            self._notified.discard("auth")
        if now < self._backoff_until:
            return

        items = self.db.next_items(WORKERS)
        if not items:
            if self.db.get_status().get("state") not in ("ok",):
                self.db.set_status("state", "ok")
            self._refresh_quota(now)
            return
        try:
            uploader = self._get_uploader()
            self._ensure_rebuilt(uploader)
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = [(item, pool.submit(self._process, uploader, item)) for item in items]
                errors = []
                for item, fut in futures:
                    try:
                        fut.result()
                    except (QuotaExceededError, AuthError, TransientDriveError) as e:
                        errors.append((item, e))
            for _, e in errors:
                raise e
        except QuotaExceededError:
            self.db.set_status("state", "drive_full")
            self._backoff_until = now + QUOTA_BACKOFF_S
            self._notify_once("quota", "Google Drive is full",
                              "Drive Backup paused uploads until space is freed.")
            return
        except AuthError:
            self.db.set_status("state", "signin_needed")
            self._waiting_for_signin = True
            self._auth_token_mtime = self._mtime(self._token_path)
            self._uploader = None
            self._notify_once("auth", "Drive Backup: sign-in needed",
                              "Open Drive Backup and sign in with Google again.")
            return
        except TransientDriveError as e:
            for item in items:
                self.db.bump_attempt(item.id)
            self._backoff_until = now + TRANSIENT_BACKOFF_S
            if self._failing_since is None:
                self._failing_since = now
            if now - self._failing_since >= FAILING_AFTER_S:
                self.db.set_status("state", "failing")
                self.db.add_warning("failing", f"Uploads have been failing for over an hour: {e}")
                self._notify_once("failing", "Drive Backup: uploads failing",
                                  "Files haven't reached Google Drive for over an hour.")
            else:
                self.db.set_status("state", "retrying")
            return

        self._failing_since = None
        self.db.clear_warning("failing")
        self._notified -= {"quota", "failing"}
        self.db.set_status("state", "ok")
        self.db.set_status("queue", str(self.db.queue_size()))
        self._refresh_quota(now)

    def _ensure_rebuilt(self, uploader: Uploader) -> None:
        """Before the first upload, reconnect to an existing backup (after a reinstall)."""
        if self._rebuild_checked:
            return
        if self.db.is_empty():
            restored = rebuild_from_drive(uploader.drive, self.db, self.cfg)
            log.info("restored %d records from an existing backup", restored)
        self._rebuild_checked = True

    def _process(self, uploader: Uploader, item) -> None:
        folder = self._folder(item.folder)
        if folder is None:          # folder no longer watched
            self.db.remove_item(item.id)
            return
        outcome = uploader.upload(self.cfg, folder, item.rel_path, forced=item.forced)
        self.db.remove_item(item.id)
        if outcome == Outcome.CHANGED_DURING_UPLOAD:
            # wait for it to settle again; a crash before then is caught by the next scan
            self.settler.touch((item.folder, item.rel_path))
        elif outcome == Outcome.UPLOADED:
            self.db.set_status("last_upload", _now_iso())

    def _refresh_quota(self, now: float) -> None:
        if now - self._last_quota < QUOTA_REFRESH_S:
            return
        self._last_quota = now
        try:
            self._get_uploader()
            used, limit = self._drive.storage_quota()
            self.db.set_status("quota_used", str(used))
            self.db.set_status("quota_limit", "" if limit is None else str(limit))
        except Exception as e:
            log.debug("quota refresh failed: %s", e)

    # -- real run --------------------------------------------------------------
    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.full_scan()
        observer = self._start_observer()

        def restart_watch():
            nonlocal observer
            self._stop_observer(observer)
            observer = self._start_observer()
        self.on_folders_changed = restart_watch

        last_scan = last_cfg = self._clock()
        try:
            while not stop.is_set():
                try:
                    now = self._clock()
                    if now - last_cfg >= CONFIG_POLL_S:
                        last_cfg = now
                        self.reload_config_if_changed()
                    if now - last_scan >= SCAN_INTERVAL_S:
                        last_scan = now
                        self.full_scan()
                    self.tick()
                except Exception:
                    log.exception("unexpected error in service loop")
                stop.wait(1.0)
        finally:
            self._stop_observer(observer)

    def _start_observer(self):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        svc = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                svc.on_fs_event(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    svc.on_fs_event(event.src_path)

            def on_moved(self, event):
                svc.on_fs_event(event.dest_path)

            def on_closed(self, event):
                svc.on_fs_event(event.src_path)

        observer = Observer()
        handler = Handler()
        for f in self.cfg.folders:
            if not os.path.isdir(f.path):
                continue
            try:
                observer.schedule(handler, f.path, recursive=True)
            except OSError as e:
                log.warning("cannot watch %s live (%s); relying on periodic scans", f.path, e)
                self.db.add_warning("watch_limit",
                                    f"Live watching unavailable for {f.path}; changes are "
                                    f"picked up by the scan every {SCAN_INTERVAL_S // 60} minutes")
        try:
            observer.start()
        except OSError as e:
            log.warning("file watcher failed to start (%s); relying on periodic scans", e)
            self.db.add_warning("watch_limit", f"Live watching unavailable: {e}")
        return observer

    @staticmethod
    def _stop_observer(observer) -> None:
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception:
            pass


def setup_logging(log_path: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
