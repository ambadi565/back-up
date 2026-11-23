"""Best-effort desktop notifications. Never raises."""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def notify(title: str, body: str) -> None:
    try:
        if sys.platform == "win32":
            t, b = title.replace("'", "''"), body.replace("'", "''")
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType=WindowsRuntime] > $null;"
                "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                f"$n = $x.GetElementsByTagName('text'); $n.Item(0).InnerText = '{t}';"
                f"$n.Item(1).InnerText = '{b}';"
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($x);"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                "'Drive Backup').Show($toast)"
            )
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                            script], timeout=15, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "Drive Backup", title, body],
                           timeout=10, capture_output=True)
    except Exception as e:  # notifications are nice-to-have
        log.debug("notification failed: %s", e)
