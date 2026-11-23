import pytest

from drive_backup.drive_client import RemoteNotFoundError, RevisionLimitError
from fake_drive import FakeDrive


def test_create_update_keeps_revisions(tmp_path):
    d = FakeDrive()
    f = tmp_path / "a.txt"
    f.write_text("one")
    folder = d.create_folder("root", "Backup")
    assert d.find_folder("root", "Backup") == folder
    r1 = d.create_file(folder, "a.txt", f, {"rel_path": "a.txt"})
    f.write_text("two")
    r2 = d.update_file(r1.file_id, f, {"rel_path": "a.txt"})
    assert r2.file_id == r1.file_id and r2.revision_id != r1.revision_id
    assert [r["content"] for r in d.files[r1.file_id]["revisions"]] == [b"one", b"two"]
    d.keep_revision(r1.file_id, r1.revision_id)
    assert d.files[r1.file_id]["revisions"][0]["keep"] is True


def test_revision_limit(tmp_path):
    d = FakeDrive(max_kept=1)
    f = tmp_path / "a.txt"
    f.write_text("1")
    r1 = d.create_file("root", "a.txt", f, {})
    d.keep_revision(r1.file_id, r1.revision_id)
    r2 = d.update_file(r1.file_id, f, {})
    with pytest.raises(RevisionLimitError):
        d.keep_revision(r2.file_id, r2.revision_id)


def test_update_deleted_raises(tmp_path):
    d = FakeDrive()
    f = tmp_path / "a.txt"
    f.write_text("1")
    r = d.create_file("root", "a.txt", f, {})
    d.delete_remote(r.file_id)
    with pytest.raises(RemoteNotFoundError):
        d.update_file(r.file_id, f, {})


def test_list_tree_nested(tmp_path):
    d = FakeDrive()
    f = tmp_path / "a.txt"
    f.write_text("1")
    top = d.create_folder("root", "top")
    sub = d.create_folder(top, "sub")
    d.create_file(top, "a.txt", f, {"rel_path": "a.txt"})
    d.create_file(sub, "b.txt", f, {"rel_path": "sub/b.txt"})
    assert sorted(r.app_properties["rel_path"] for r in d.list_tree(top)) == ["a.txt", "sub/b.txt"]
