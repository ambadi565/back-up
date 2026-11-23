"""Settings window: pick folders, sign in with Google, see status."""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from . import autostart, paths
from .config import add_folder, load_config, remove_folder, save_config
from .state import StateDB

STATE_TEXT = {
    "ok": "Up to date / uploading",
    "paused": "Paused",
    "drive_full": "Google Drive is full",
    "signin_needed": "Sign-in needed",
    "retrying": "Connection problem, retrying",
    "failing": "Uploads failing for over an hour",
}


def _human(n: str | None) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024:
            return f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} PB"


class _SignInWorker(QObject):
    done = Signal(str, str)  # email, error

    def run(self):
        from .auth import sign_in, signed_in_email
        try:
            creds = sign_in(paths.client_secret_file(), paths.token_file())
            self.done.emit(signed_in_email(creds) or "your Google account", "")
        except Exception as e:
            self.done.emit("", str(e))


class _EmailWorker(QObject):
    done = Signal(str)

    def run(self):
        from .auth import load_credentials, signed_in_email
        try:
            creds = load_credentials(paths.token_file())
            self.done.emit((signed_in_email(creds) or "") if creds else "")
        except Exception:
            self.done.emit("")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Drive Backup")
        self.resize(560, 560)
        self.db = StateDB(paths.db_file())
        self._threads: list[QThread] = []

        central = QWidget()
        layout = QVBoxLayout(central)

        # account
        acct = QGroupBox("Google account")
        al = QHBoxLayout(acct)
        self.account_label = QLabel("Checking…")
        self.signin_btn = QPushButton("Sign in with Google")
        self.signin_btn.clicked.connect(self.sign_in)
        al.addWidget(self.account_label, 1)
        al.addWidget(self.signin_btn)
        layout.addWidget(acct)

        # folders
        fbox = QGroupBox("Folders to back up")
        fl = QVBoxLayout(fbox)
        self.folder_list = QListWidget()
        fl.addWidget(self.folder_list)
        row = QHBoxLayout()
        add_btn = QPushButton("Add folder…")
        add_btn.clicked.connect(self.add_folder)
        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(self.remove_folder)
        row.addWidget(add_btn)
        row.addWidget(rm_btn)
        row.addStretch(1)
        fl.addLayout(row)
        fl.addWidget(QLabel("Removing a folder stops backing it up. "
                            "Nothing already on Google Drive is ever deleted."))
        layout.addWidget(fbox)

        # options
        self.pause_box = QCheckBox("Pause uploads")
        self.pause_box.toggled.connect(self.toggle_pause)
        self.autostart_box = QCheckBox("Start automatically at login")
        self.autostart_box.toggled.connect(self.toggle_autostart)
        layout.addWidget(self.pause_box)
        layout.addWidget(self.autostart_box)

        # status
        sbox = QGroupBox("Status")
        sl = QVBoxLayout(sbox)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.warn_list = QListWidget()
        sl.addWidget(self.status_label)
        sl.addWidget(QLabel("Warnings:"))
        sl.addWidget(self.warn_list)
        layout.addWidget(sbox, 1)

        self.setCentralWidget(central)
        self._load()
        self._run_bg(_EmailWorker(), self._email_loaded)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(2000)
        self.refresh_status()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        for t in list(self._threads):
            t.quit()
            t.wait(5000)
        super().closeEvent(event)

    # -- helpers ---------------------------------------------------------------
    def _run_bg(self, worker: QObject, slot) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(slot)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self._threads.remove(thread))
        thread._worker = worker  # keep a reference
        self._threads.append(thread)
        thread.start()

    def _load(self) -> None:
        self.cfg = load_config(paths.config_file())
        self.folder_list.clear()
        for f in self.cfg.folders:
            self.folder_list.addItem(f"{f.path}   →   Drive Backup/{self.cfg.computer_name}/"
                                     f"{f.drive_name}")
        self.pause_box.blockSignals(True)
        self.pause_box.setChecked(self.cfg.paused)
        self.pause_box.blockSignals(False)
        self.autostart_box.blockSignals(True)
        try:
            self.autostart_box.setChecked(autostart.is_installed())
        except Exception:
            self.autostart_box.setChecked(False)
        self.autostart_box.blockSignals(False)

    def _save(self) -> None:
        save_config(self.cfg, paths.config_file())
        self._load()

    # -- actions ---------------------------------------------------------------
    def _email_loaded(self, email: str) -> None:
        self.account_label.setText(f"Signed in as {email}" if email else "Not signed in")
        self.signin_btn.setText("Sign in again" if email else "Sign in with Google")

    def sign_in(self) -> None:
        if not paths.client_secret_file().exists():
            QMessageBox.warning(
                self, "Google setup needed",
                f"Put your client_secret.json here first:\n{paths.client_secret_file()}\n\n"
                "See 'Google Cloud setup' in the README.")
            return
        self.signin_btn.setEnabled(False)
        self.account_label.setText("Waiting for you to sign in in the browser…")
        self._run_bg(_SignInWorker(), self._signed_in)

    def _signed_in(self, email: str, error: str) -> None:
        self.signin_btn.setEnabled(True)
        if error:
            self.account_label.setText("Not signed in")
            QMessageBox.warning(self, "Sign-in failed", error)
        else:
            self._email_loaded(email)

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to back up")
        if not path:
            return
        try:
            self.cfg = add_folder(self.cfg, path)
        except ValueError as e:
            QMessageBox.warning(self, "Can't add folder", str(e))
            return
        self._save()

    def remove_folder(self) -> None:
        row = self.folder_list.currentRow()
        if row < 0:
            return
        folder = self.cfg.folders[row]
        if QMessageBox.question(
                self, "Stop backing up?",
                f"Stop backing up {folder.path}?\nFiles already on Google Drive stay there."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.cfg = remove_folder(self.cfg, folder.path)
        self._save()

    def toggle_pause(self, checked: bool) -> None:
        self.cfg.paused = checked
        self._save()

    def toggle_autostart(self, checked: bool) -> None:
        try:
            autostart.install() if checked else autostart.uninstall()
        except Exception as e:
            QMessageBox.warning(self, "Autostart", f"Could not change autostart:\n{e}")
        self._load()

    def refresh_status(self) -> None:
        st = self.db.get_status()
        try:
            running = autostart.is_running()
        except Exception:
            running = False
        lines = [
            f"Background service: {'running' if running else 'NOT running'}",
            f"State: {STATE_TEXT.get(st.get('state', ''), st.get('state', 'unknown'))}",
            f"Files waiting: {st.get('queue', '0')} queued, {st.get('pending', '0')} settling",
            f"Last upload: {st.get('last_upload', 'never')}",
            f"Last full scan: {st.get('last_scan', 'never')}",
        ]
        if st.get("quota_used"):
            limit = st.get("quota_limit")
            lines.append(f"Google Drive used: {_human(st['quota_used'])}"
                         + (f" of {_human(limit)}" if limit else ""))
        self.status_label.setText("\n".join(lines))
        self.warn_list.clear()
        for msg in self.db.warnings().values():
            self.warn_list.addItem(msg)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Drive Backup")
    app.setDesktopFileName("drive-backup")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
