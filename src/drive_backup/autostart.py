"""Start the background service at login: systemd --user on Linux, Task Scheduler on Windows."""
from __future__ import annotations

import shlex
import subprocess
import sys
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
        r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _python_cmd("service", True),
                  "/SC", "ONLOGON", "/RL", "LIMITED", "/F"])
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
