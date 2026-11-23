"""Which files never get uploaded (temp, lock and OS junk files)."""
from __future__ import annotations

from fnmatch import fnmatch

DEFAULT_IGNORES: list[str] = [
    "~$*", "*.tmp", "*.swp", "*.swx", ".~lock.*", "*.part", "*.crdownload",
    ".DS_Store", "Thumbs.db", "desktop.ini",
]


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    parts = [p for p in rel_path.split("/") if p]
    for pattern in (*DEFAULT_IGNORES, *patterns):
        if any(fnmatch(part, pattern) for part in parts):
            return True
    return False
