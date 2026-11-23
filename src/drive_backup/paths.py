"""Where Drive Backup keeps its config, data, token and logs."""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "drive-backup"


def _override() -> Path | None:
    home = os.environ.get("DRIVE_BACKUP_HOME")
    return Path(home) if home else None


def config_dir() -> Path:
    d = _override() or Path(user_config_dir(APP_NAME, appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = _override() or Path(user_data_dir(APP_NAME, appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return config_dir() / "config.json"


def token_file() -> Path:
    return config_dir() / "token.json"


def client_secret_file() -> Path:
    return config_dir() / "client_secret.json"


def db_file() -> Path:
    return data_dir() / "state.db"


def log_file() -> Path:
    return data_dir() / "drive-backup.log"
