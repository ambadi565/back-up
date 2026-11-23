from drive_backup.ignore import is_ignored


def test_office_lock_file_ignored():
    assert is_ignored("a/~$doc.docx", [])


def test_normal_file_not_ignored():
    assert not is_ignored("a/report.docx", [])


def test_user_pattern_ignores_subtree():
    assert is_ignored("x/node_modules/y.js", ["node_modules"])


def test_swap_file_ignored():
    assert is_ignored("notes.swp", [])
