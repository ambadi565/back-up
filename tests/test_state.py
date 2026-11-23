from drive_backup.state import FileRecord, StateDB


def test_file_roundtrip(tmp_path):
    db = StateDB(tmp_path / "s.db")
    rec = FileRecord("Docs", "a/b.txt", 10, 123, "fid", 1, 99.0)
    db.upsert_file(rec)
    assert db.get_file("Docs", "a/b.txt") == rec
    assert db.get_file("Docs", "nope") is None
    assert not db.is_empty()
    assert db.all_files("Docs") == [rec]


def test_enqueue_dedupes(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.enqueue("Docs", "a.txt")
    db.enqueue("Docs", "a.txt", forced=True)
    assert db.queue_size() == 1
    [item] = db.next_items(10)
    assert item.forced is True
    db.bump_attempt(item.id)
    assert db.next_items(10)[0].attempts == 1
    db.remove_item(item.id)
    assert db.queue_size() == 0


def test_queue_survives_reopen(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.enqueue("Docs", "a.txt")
    db.close()
    db2 = StateDB(tmp_path / "s.db")
    assert [(i.folder, i.rel_path) for i in db2.next_items(10)] == [("Docs", "a.txt")]


def test_warnings_set_and_clear(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.add_warning("k", "msg")
    assert db.warnings() == {"k": "msg"}
    db.clear_warning("k")
    assert db.warnings() == {}


def test_status_and_folder_ids(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.set_status("state", "ok")
    assert db.get_status()["state"] == "ok"
    db.set_folder_id("Drive Backup/h/Docs", "F1")
    assert db.get_folder_id("Drive Backup/h/Docs") == "F1"
    assert db.get_folder_id("x") is None
