"""Start the background service at login: systemd --user on Linux, Task Scheduler on Windows."""
from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape
from pathlib import Path

UNIT_NAME = "drive-backup.service"
TASK_NAME = "Drive Backup"


def _python_cmd(sub: str, windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            exe = w
    if sys.platform == "win32":
        return f'"{exe}" -m drive_backup.cli {sub}'
    return f"{shlex.quote(str(exe))} -m drive_backup.cli {sub}"


def systemd_unit_text(command: str) -> str:
    return (
        "[Unit]\n"
        "Description=Drive Backup (push changed files to Google Drive)\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def desktop_entry_text(command: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Drive Backup\n"
        "Comment=Back up chosen folders to Google Drive\n"
        f"Exec={command}\n"
        "Icon=folder-cloud\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )


def windows_task_xml(command: str, arguments: str) -> str:
    """Task Scheduler definition: run at logon, also on battery, no time limit, restart on failure."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Drive Backup: push changed files to Google Drive</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd></IdleSettings>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(command)}</Command><Arguments>{escape(arguments)}</Arguments></Exec></Actions>
</Task>
"""


def _windows_exe() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _desktop_path() -> Path:
    return Path.home() / ".local" / "share" / "applications" / "drive-backup.desktop"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, **kw)


def install_launcher() -> None:
    """Linux: add 'Drive Backup' to the app launcher."""
    if sys.platform.startswith("linux"):
        p = _desktop_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(desktop_entry_text(_python_cmd("gui")), encoding="utf-8")


def install() -> None:
    if sys.platform == "win32":
        xml = windows_task_xml(_windows_exe(), "-m drive_backup.cli service")
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                         encoding="utf-16") as f:
            f.write(xml)
        try:
            r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", f.name, "/F"])
        finally:
            Path(f.name).unlink(missing_ok=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        _run(["schtasks", "/Run", "/TN", TASK_NAME])
        return
    p = _unit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(systemd_unit_text(_python_cmd("service")), encoding="utf-8")
    install_launcher()
    _run(["systemctl", "--user", "daemon-reload"])
    r = _run(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


def uninstall() -> None:
    if sys.platform == "win32":
        _run(["schtasks", "/End", "/TN", TASK_NAME])
        _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        return
    _run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    try:
        _unit_path().unlink()
    except FileNotFoundError:
        pass
    _run(["systemctl", "--user", "daemon-reload"])


def is_installed() -> bool:
    if sys.platform == "win32":
        return _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0
    return _run(["systemctl", "--user", "is-enabled", UNIT_NAME]).stdout.strip() == "enabled"


def is_running() -> bool:
    if sys.platform == "win32":
        r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "CSV", "/NH"])
        return "Running" in r.stdout
    return _run(["systemctl", "--user", "is-active", UNIT_NAME]).stdout.strip() == "active"
