"""After a reinstall or a dead disk: rebuild local state from what's already on Drive."""
from __future__ import annotations

import logging

from .config import Config
from .drive_client import DriveClient
from .state import FileRecord, StateDB
from .uploader import ROOT_NAME

log = logging.getLogger(__name__)


def rebuild_from_drive(drive: DriveClient, db: StateDB, cfg: Config) -> int:
    if not db.is_empty():
        return 0
    root = drive.find_folder("root", ROOT_NAME)
    if not root:
        return 0
    host = drive.find_folder(root, cfg.computer_name)
    if not host:
        return 0
    db.set_folder_id(ROOT_NAME, root)
    db.set_folder_id(f"{ROOT_NAME}/{cfg.computer_name}", host)
    names = {f.drive_name for f in cfg.folders}
    count = 0
    for rf in drive.list_tree(host):
        p = rf.app_properties
        if p.get("folder") not in names or not p.get("rel_path"):
            continue
        try:
            size, mtime_ns = int(p["size"]), int(p["mtime_ns"])
        except (KeyError, ValueError):
            continue
        db.upsert_file(FileRecord(p["folder"], p["rel_path"], size, mtime_ns,
                                  rf.file_id, 0, 0.0))
        count += 1
    log.info("rebuilt %d file records from Drive", count)
    return count
