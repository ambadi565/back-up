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


def test_windows_task_xml():
    from drive_backup.autostart import windows_task_xml
    xml = windows_task_xml(r"C:\venv\Scripts\pythonw.exe", "-m drive_backup.cli service")
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "<RestartOnFailure>" in xml
    assert "<LogonTrigger>" in xml
    assert r"<Command>C:\venv\Scripts\pythonw.exe</Command>" in xml
    assert "<Arguments>-m drive_backup.cli service</Arguments>" in xml
