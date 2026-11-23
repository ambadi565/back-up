"""Full scan of a watched folder: which files differ from what was uploaded."""
from __future__ import annotations

import logging
import os

from .config import WatchedFolder
from .ignore import is_ignored
from .state import StateDB

log = logging.getLogger(__name__)


def to_rel(folder_path: str, abs_path: str) -> str:
    return os.path.relpath(abs_path, folder_path).replace(os.sep, "/")


def scan_folder(folder: WatchedFolder, db: StateDB, patterns: list[str]) -> list[str] | None:
    if not os.path.isdir(folder.path):
        return None
    changed: list[str] = []
    for dirpath, dirnames, filenames in os.walk(folder.path, followlinks=False):
        # prune ignored dirs and symlinked dirs (os.walk lists them but won't descend)
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(to_rel(folder.path, os.path.join(dirpath, d)), patterns)
        ]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel = to_rel(folder.path, abs_path)
            if is_ignored(rel, patterns):
                continue
            try:
                st = os.lstat(abs_path)
            except OSError as e:
                log.warning("cannot stat %s: %s", abs_path, e)
                continue
            if os.path.islink(abs_path) or not os.path.isfile(abs_path):
                continue
            rec = db.get_file(folder.drive_name, rel)
            if rec is None or rec.size != st.st_size or rec.mtime_ns != st.st_mtime_ns:
                changed.append(rel)
    return changed
