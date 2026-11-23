from drive_backup.autostart import desktop_entry_text, systemd_unit_text


def test_systemd_unit_text():
    text = systemd_unit_text("/x/python -m drive_backup.cli service")
    assert "ExecStart=/x/python -m drive_backup.cli service" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text


def test_desktop_entry_text():
    text = desktop_entry_text("/x/python -m drive_backup.cli gui")
    assert "Name=Drive Backup" in text
    assert "Exec=/x/python -m drive_backup.cli gui" in text
