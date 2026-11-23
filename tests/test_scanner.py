import os
import sys

import pytest

from drive_backup.config import WatchedFolder
from drive_backup.scanner import scan_folder, to_rel
from drive_backup.state import FileRecord, StateDB


def _record(db, folder, root, rel):
    st = os.stat(root / rel)
    db.upsert_file(FileRecord(folder.drive_name, rel, st.st_size, st.st_mtime_ns, "id", 1, 0.0))


def test_new_and_changed_detected(tmp_path):
    root = tmp_path / "Docs"
    root.mkdir()
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    db = StateDB(tmp_path / "s.db")
    folder = WatchedFolder(str(root), "Docs")
    _record(db, folder, root, "a.txt")
    assert scan_folder(folder, db, []) == ["b.txt"]
    (root / "a.txt").write_text("changed!")
    assert sorted(scan_folder(folder, db, [])) == ["a.txt", "b.txt"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_ignored_and_symlinks_skipped(tmp_path):
    root = tmp_path / "Docs"
    root.mkdir()
    (root / "keep.txt").write_text("k")
    (root / "x.tmp").write_text("t")
    (root / "link.txt").symlink_to(root / "keep.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.txt").write_text("o")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    db = StateDB(tmp_path / "s.db")
    assert scan_folder(WatchedFolder(str(root), "Docs"), db, []) == ["keep.txt"]


def test_missing_folder_returns_none(tmp_path):
    db = StateDB(tmp_path / "s.db")
    assert scan_folder(WatchedFolder(str(tmp_path / "gone"), "Gone"), db, []) is None


def test_rel_paths_are_posix_and_unicode(tmp_path):
    root = tmp_path / "Docs"
    (root / "sub dir").mkdir(parents=True)
    (root / "sub dir" / "résumé ✓.txt").write_text("x")
    db = StateDB(tmp_path / "s.db")
    assert scan_folder(WatchedFolder(str(root), "Docs"), db, []) == ["sub dir/résumé ✓.txt"]
    sep = os.sep
    assert to_rel(f"{sep}x", f"{sep}x{sep}a{sep}b.txt") == "a/b.txt"
