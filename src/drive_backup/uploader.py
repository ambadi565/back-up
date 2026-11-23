"""Upload one local file to Google Drive, keeping every version. Never deletes."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TypeVar

from .config import Config, WatchedFolder
from .drive_client import (
    DriveClient, RemoteNotFoundError, RevisionLimitError, TransientDriveError,
)
from .state import FileRecord, StateDB

log = logging.getLogger(__name__)

ROOT_NAME = "Drive Backup"
RETRY_DELAYS = [1, 2, 4, 8, 16]

T = TypeVar("T")


class Outcome(Enum):
    UPLOADED = "uploaded"
    UNCHANGED = "unchanged"
    VANISHED = "vanished"
    CHANGED_DURING_UPLOAD = "changed_during_upload"
    UNREADABLE = "unreadable"


def _sig(path: str) -> tuple[int, int] | None:
    try:
        if os.path.islink(path):
            return None
        st = os.stat(path)
    except FileNotFoundError:
        return None
    if not os.path.isfile(path):
        return None
    return st.st_size, st.st_mtime_ns


class Uploader:
    def __init__(self, drive: DriveClient, db: StateDB,
                 sleep: Callable[[float], None] = time.sleep):
        self.drive = drive
        self.db = db
        self._sleep = sleep
        self._folder_lock = threading.Lock()

    def _retry(self, fn: Callable[[], T]) -> T:
        for delay in [*RETRY_DELAYS, None]:
            try:
                return fn()
            except TransientDriveError as e:
                if delay is None:
                    raise
                log.info("transient Drive error, retrying in %ss: %s", delay, e)
                self._sleep(delay)
        raise AssertionError("unreachable")

    def ensure_folder(self, parts: list[str]) -> str:
        with self._folder_lock:
            return self._ensure_folder(parts)

    def _ensure_folder(self, parts: list[str]) -> str:
        parent = "root"
        for i in range(len(parts)):
            key = "/".join(parts[: i + 1])
            cached = self.db.get_folder_id(key)
            if cached:
                parent = cached
                continue
            name = parts[i]
            found = self._retry(lambda: self.drive.find_folder(parent, name))
            if not found:
                found = self._retry(lambda: self.drive.create_folder(parent, name))
            self.db.set_folder_id(key, found)
            parent = found
        return parent

    def upload(self, cfg: Config, folder: WatchedFolder, rel_path: str,
               forced: bool = False) -> Outcome:
        abs_path = os.path.join(folder.path, *rel_path.split("/"))
        before = _sig(abs_path)
        if before is None:
            return Outcome.VANISHED
        rec = self.db.get_file(folder.drive_name, rel_path)
        if rec and rec.drive_id and (rec.size, rec.mtime_ns) == before:
            return Outcome.UNCHANGED

        key = f"{folder.drive_name}/{rel_path}"
        props = {"rel_path": rel_path, "folder": folder.drive_name,
                 "size": str(before[0]), "mtime_ns": str(before[1])}
        root_parts = [ROOT_NAME, cfg.computer_name, folder.drive_name]
        dir_parts = rel_path.split("/")[:-1]
        name = rel_path.split("/")[-1]
        kept = rec.kept_revisions if rec else 0
        drive_id = rec.drive_id if rec else ""

        try:
            result = None
            if drive_id:
                try:
                    result = self._retry(
                        lambda: self.drive.update_file(drive_id, Path(abs_path), props))
                except RemoteNotFoundError:
                    log.info("%s missing on Drive, re-creating", key)
                    kept = 0
            if result is None:
                try:
                    result = self._create(root_parts + dir_parts, name, rel_path,
                                          Path(abs_path), props)
                except RemoteNotFoundError:
                    # a cached Drive folder was deleted or trashed by the user:
                    # forget every cached folder id and resolve the path again
                    self.db.clear_folder_ids(ROOT_NAME)
                    result = self._create(root_parts + dir_parts, name, rel_path,
                                          Path(abs_path), props)
        except OSError as e:
            log.warning("cannot read %s: %s", abs_path, e)
            self.db.add_warning(f"locked:{key}", f"Could not read {rel_path}: {e.strerror or e}")
            return Outcome.UNREADABLE

        after = _sig(abs_path)
        changed = after != before
        if changed and not forced:
            # Drive got an in-between copy. Remember the Drive file (so the retry updates it
            # instead of creating a duplicate) but not the size/mtime, so it gets uploaded again.
            self.db.upsert_file(FileRecord(folder.drive_name, rel_path, -1, -1,
                                           result.file_id, kept, time.time()))
            self.db.enqueue(folder.drive_name, rel_path)
            return Outcome.CHANGED_DURING_UPLOAD

        try:
            self._retry(lambda: self.drive.keep_revision(result.file_id, result.revision_id))
            kept += 1
        except RevisionLimitError:
            self.db.add_warning(
                f"revlimit:{key}",
                f"Older versions of {rel_path} beyond 200 are only kept ~30 days by Google")

        self.db.upsert_file(FileRecord(folder.drive_name, rel_path, before[0], before[1],
                                       result.file_id, kept, time.time()))
        self.db.clear_warning(f"locked:{key}")
        if forced:
            self.db.add_warning(f"unsettled:{key}",
                                f"{rel_path} kept changing; uploaded a best-effort copy")
        else:
            self.db.clear_warning(f"unsettled:{key}")
        return Outcome.UPLOADED

    def _create(self, parts: list[str], name: str, rel_path: str, local_path: Path,
                props: dict[str, str]):
        parent = self.ensure_folder(parts)
        # A previous run may have created it but died before recording it: reuse that file.
        existing = self._retry(lambda: self.drive.find_file(parent, rel_path))
        if existing:
            try:
                return self._retry(lambda: self.drive.update_file(existing, local_path, props))
            except RemoteNotFoundError:
                pass
        return self._retry(lambda: self.drive.create_file(parent, name, local_path, props))
