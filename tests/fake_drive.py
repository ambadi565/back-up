"""In-memory DriveClient for tests."""
from __future__ import annotations

import itertools
from pathlib import Path

from drive_backup.drive_client import (
    QuotaExceededError, RemoteFile, RemoteNotFoundError, RevisionLimitError, UploadResult,
)


class FakeDrive:
    def __init__(self, max_kept: int = 200, quota_limit: int | None = None):
        self.files: dict[str, dict] = {}
        self.folders: dict[str, dict] = {}   # id -> {parent, name}
        self.fail_next: list[Exception] = []
        self.max_kept = max_kept
        self.quota_limit = quota_limit
        self._ids = itertools.count(1)
        self.calls: list[str] = []

    def _maybe_fail(self, op: str):
        self.calls.append(op)
        if self.fail_next:
            raise self.fail_next.pop(0)

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def _used(self) -> int:
        return sum(len(r["content"]) for f in self.files.values() for r in f["revisions"])

    def _check_quota(self, extra: int):
        if self.quota_limit is not None and self._used() + extra > self.quota_limit:
            raise QuotaExceededError("full")

    def find_folder(self, parent_id, name):
        self._maybe_fail("find_folder")
        for fid, f in self.folders.items():
            if f["parent"] == parent_id and f["name"] == name:
                return fid
        return None

    def create_folder(self, parent_id, name):
        self._maybe_fail("create_folder")
        fid = self._id("D")
        self.folders[fid] = {"parent": parent_id, "name": name}
        return fid

    def create_file(self, parent_id, name, local_path: Path, app_properties):
        self._maybe_fail("create_file")
        content = Path(local_path).read_bytes()
        self._check_quota(len(content))
        fid, rid = self._id("F"), self._id("R")
        self.files[fid] = {"parent": parent_id, "name": name, "props": dict(app_properties),
                           "revisions": [{"id": rid, "content": content, "keep": False}]}
        return UploadResult(fid, rid)

    def update_file(self, file_id, local_path: Path, app_properties):
        self._maybe_fail("update_file")
        if file_id not in self.files:
            raise RemoteNotFoundError(file_id)
        content = Path(local_path).read_bytes()
        self._check_quota(len(content))
        rid = self._id("R")
        f = self.files[file_id]
        f["props"] = dict(app_properties)
        f["revisions"].append({"id": rid, "content": content, "keep": False})
        return UploadResult(file_id, rid)

    def keep_revision(self, file_id, revision_id):
        self._maybe_fail("keep_revision")
        f = self.files[file_id]
        if sum(r["keep"] for r in f["revisions"]) >= self.max_kept:
            raise RevisionLimitError("limit")
        for r in f["revisions"]:
            if r["id"] == revision_id:
                r["keep"] = True

    def list_tree(self, root_id):
        self._maybe_fail("list_tree")
        stack = [root_id]
        while stack:
            p = stack.pop()
            stack.extend(fid for fid, f in self.folders.items() if f["parent"] == p)
            for fid, f in self.files.items():
                if f["parent"] == p:
                    yield RemoteFile(fid, p, f["name"], dict(f["props"]))

    def storage_quota(self):
        return self._used(), self.quota_limit

    # test helpers
    def delete_remote(self, file_id):
        del self.files[file_id]

    def path_of(self, file_id) -> str:
        parts = [self.files[file_id]["name"]]
        p = self.files[file_id]["parent"]
        while p != "root":
            parts.append(self.folders[p]["name"])
            p = self.folders[p]["parent"]
        return "/".join(reversed(parts))

    def by_path(self, path: str) -> dict | None:
        for fid in self.files:
            if self.path_of(fid) == path:
                return {"id": fid, **self.files[fid]}
        return None
