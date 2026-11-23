"""Local SQLite state: what has been uploaded, the pending queue, status and warnings."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileRecord:
    folder: str
    rel_path: str
    size: int
    mtime_ns: int
    drive_id: str
    kept_revisions: int
    uploaded_at: float


@dataclass
class QueueItem:
    id: int
    folder: str
    rel_path: str
    attempts: int
    forced: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  folder TEXT NOT NULL, rel_path TEXT NOT NULL, size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL, drive_id TEXT NOT NULL, kept_revisions INTEGER NOT NULL,
  uploaded_at REAL NOT NULL, PRIMARY KEY (folder, rel_path));
CREATE TABLE IF NOT EXISTS queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, folder TEXT NOT NULL, rel_path TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, forced INTEGER NOT NULL DEFAULT 0,
  next_try_at REAL NOT NULL DEFAULT 0, UNIQUE (folder, rel_path));
CREATE TABLE IF NOT EXISTS folders (drive_path TEXT PRIMARY KEY, folder_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS warnings (key TEXT PRIMARY KEY, message TEXT NOT NULL);
"""


class StateDB:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(queue)")}
        if "next_try_at" not in cols:
            self._conn.execute("ALTER TABLE queue ADD COLUMN next_try_at REAL NOT NULL DEFAULT 0")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    # files
    def get_file(self, folder: str, rel_path: str) -> FileRecord | None:
        rows = self._exec("SELECT * FROM files WHERE folder=? AND rel_path=?", (folder, rel_path))
        return FileRecord(*rows[0]) if rows else None

    def upsert_file(self, rec: FileRecord) -> None:
        self._exec(
            "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)",
            (rec.folder, rec.rel_path, rec.size, rec.mtime_ns, rec.drive_id,
             rec.kept_revisions, rec.uploaded_at),
        )

    def all_files(self, folder: str) -> list[FileRecord]:
        return [FileRecord(*r) for r in self._exec(
            "SELECT * FROM files WHERE folder=? ORDER BY rel_path", (folder,))]

    def is_empty(self) -> bool:
        return not self._exec("SELECT 1 FROM files LIMIT 1")

    # queue
    def enqueue(self, folder: str, rel_path: str, forced: bool = False) -> None:
        self._exec(
            "INSERT INTO queue (folder, rel_path, forced) VALUES (?,?,?) "
            "ON CONFLICT(folder, rel_path) DO UPDATE SET forced = forced OR excluded.forced",
            (folder, rel_path, int(forced)),
        )

    def next_items(self, limit: int, now: float | None = None,
                   exclude: set[int] | frozenset[int] = frozenset()) -> list[QueueItem]:
        """Oldest items that are due (next_try_at <= now), skipping ids in `exclude`."""
        ex = sorted(exclude)
        sql = "SELECT id, folder, rel_path, attempts, forced FROM queue WHERE 1=1"
        args: list = []
        if now is not None:
            sql += " AND next_try_at <= ?"
            args.append(now)
        if ex:
            sql += f" AND id NOT IN ({','.join('?' * len(ex))})"
            args.extend(ex)
        sql += " ORDER BY next_try_at, id LIMIT ?"
        args.append(limit)
        rows = self._exec(sql, tuple(args))
        return [QueueItem(r[0], r[1], r[2], r[3], bool(r[4])) for r in rows]

    def remove_item(self, item_id: int) -> None:
        self._exec("DELETE FROM queue WHERE id=?", (item_id,))

    def bump_attempt(self, item_id: int) -> None:
        self._exec("UPDATE queue SET attempts = attempts + 1 WHERE id=?", (item_id,))

    def defer(self, item_id: int, until: float) -> None:
        """Count a failed attempt and don't retry this item before `until`."""
        self._exec("UPDATE queue SET attempts = attempts + 1, next_try_at = ? WHERE id=?",
                   (until, item_id))

    def queue_size(self) -> int:
        return self._exec("SELECT COUNT(*) FROM queue")[0][0]

    # drive folder id cache
    def get_folder_id(self, drive_path: str) -> str | None:
        rows = self._exec("SELECT folder_id FROM folders WHERE drive_path=?", (drive_path,))
        return rows[0][0] if rows else None

    def set_folder_id(self, drive_path: str, folder_id: str) -> None:
        self._exec("INSERT OR REPLACE INTO folders VALUES (?,?)", (drive_path, folder_id))

    def clear_folder_ids(self, prefix: str) -> None:
        self._exec("DELETE FROM folders WHERE drive_path = ? OR drive_path LIKE ?",
                   (prefix, prefix + "/%"))

    # status / warnings
    def set_status(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO status VALUES (?,?)", (key, str(value)))

    def get_status(self) -> dict[str, str]:
        return dict(self._exec("SELECT key, value FROM status"))

    def add_warning(self, key: str, message: str) -> None:
        self._exec("INSERT OR REPLACE INTO warnings VALUES (?,?)", (key, message))

    def clear_warning(self, key: str) -> None:
        self._exec("DELETE FROM warnings WHERE key=?", (key,))

    def warnings(self) -> dict[str, str]:
        return dict(self._exec("SELECT key, message FROM warnings ORDER BY key"))
