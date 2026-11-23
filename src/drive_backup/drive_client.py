"""Google Drive access: a small protocol, typed errors, and the real implementation.

Nothing in this module deletes, trashes or un-keeps anything on Drive.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

log = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024
RESUMABLE_THRESHOLD = 5 * 1024 * 1024


@dataclass
class UploadResult:
    file_id: str
    revision_id: str


@dataclass
class RemoteFile:
    file_id: str
    parent_id: str
    name: str
    app_properties: dict[str, str]


class DriveError(Exception):
    pass


class TransientDriveError(DriveError):
    """Network trouble, rate limits, server errors: try again later."""


class QuotaExceededError(DriveError):
    """Google Drive is full."""


class AuthError(DriveError):
    """Sign-in expired or revoked: the user must sign in again."""


class RemoteNotFoundError(DriveError):
    """The file or folder no longer exists on Drive."""


class RevisionLimitError(DriveError):
    """Drive refused to keep another revision forever (200 per file)."""


class DriveClient(Protocol):
    def find_folder(self, parent_id: str, name: str) -> str | None: ...
    def find_file(self, parent_id: str, rel_path: str) -> str | None: ...
    def find_file(self, parent_id: str, rel_path: str) -> str | None:
        q = (f"'{_q(parent_id)}' in parents and trashed = false and "
             f"appProperties has {{ key='rel_path' and value='{_q(rel_path)}' }}")
        res = self._call(lambda: self._service.files().list(
            q=q, fields="files(id)", pageSize=10, spaces="drive").execute(num_retries=3))
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def create_folder(self, parent_id: str, name: str) -> str: ...
    def create_file(self, parent_id: str, name: str, local_path: Path,
                    app_properties: dict[str, str]) -> UploadResult: ...
    def update_file(self, file_id: str, local_path: Path,
                    app_properties: dict[str, str]) -> UploadResult: ...
    def keep_revision(self, file_id: str, revision_id: str) -> None: ...
    def list_tree(self, root_id: str) -> Iterator[RemoteFile]: ...
    def storage_quota(self) -> tuple[int, int | None]: ...


_TRANSIENT_REASONS = {
    "rateLimitExceeded", "userRateLimitExceeded", "backendError",
    "internalError", "sharingRateLimitExceeded",
}


def _reasons(e) -> list[str]:
    try:
        data = json.loads(e.content.decode("utf-8", "replace"))
        return [err.get("reason", "") for err in data.get("error", {}).get("errors", [])]
    except Exception:
        return []


def _map_http_error(e) -> DriveError:
    status = int(getattr(e.resp, "status", 0) or 0)
    reasons = _reasons(e)
    msg = f"HTTP {status} {','.join(reasons)}: {e}"
    if "storageQuotaExceeded" in reasons or "quotaExceeded" in reasons:
        return QuotaExceededError(msg)
    if status == 429 or status >= 500 or any(r in _TRANSIENT_REASONS for r in reasons):
        return TransientDriveError(msg)
    if status == 401:
        return AuthError(msg)
    if status == 404:
        return RemoteNotFoundError(msg)
    return DriveError(msg)


T = TypeVar("T")


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveClient:
    def __init__(self, creds):
        self._creds = creds
        self._local = threading.local()

    @property
    def _service(self):
        # httplib2 (used by googleapiclient) is not thread-safe: one service object per thread.
        svc = getattr(self._local, "service", None)
        if svc is None:
            from googleapiclient import discovery
            svc = discovery.build("drive", "v3", credentials=self._creds, cache_discovery=False)
            self._local.service = svc
        return svc

    # -- error translation -------------------------------------------------
    def _call(self, fn: Callable[[], T]) -> T:
        from google.auth.exceptions import RefreshError, TransportError
        from googleapiclient.errors import HttpError
        import httplib2
        try:
            return fn()
        except HttpError as e:
            raise _map_http_error(e) from e
        except RefreshError as e:
            raise AuthError(str(e)) from e
        except (TransportError, httplib2.HttpLib2Error, socket.timeout, TimeoutError,
                ConnectionError) as e:
            raise TransientDriveError(str(e)) from e
        except OSError as e:
            # local file problems (permission, locked) must not look like network errors
            if getattr(e, "filename", None):
                raise
            raise TransientDriveError(str(e)) from e

    def _media(self, local_path: Path):
        from googleapiclient.http import MediaFileUpload
        mime = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        size = os.path.getsize(local_path)
        if size > RESUMABLE_THRESHOLD:
            return MediaFileUpload(str(local_path), mimetype=mime, resumable=True, chunksize=CHUNK)
        return MediaFileUpload(str(local_path), mimetype=mime, resumable=False)

    @staticmethod
    def _run(request) -> dict:
        if getattr(request, "resumable", None) is not None:
            response = None
            while response is None:
                _, response = request.next_chunk(num_retries=3)
            return response
        return request.execute(num_retries=3)

    # -- protocol ------------------------------------------------------------
    def find_folder(self, parent_id: str, name: str) -> str | None:
        q = (f"'{_q(parent_id)}' in parents and name = '{_q(name)}' "
             f"and mimeType = '{FOLDER_MIME}' and trashed = false")
        res = self._call(lambda: self._service.files().list(
            q=q, fields="files(id)", pageSize=10, spaces="drive").execute(num_retries=3))
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def find_file(self, parent_id: str, rel_path: str) -> str | None:
        q = (f"'{_q(parent_id)}' in parents and trashed = false and "
             f"appProperties has {{ key='rel_path' and value='{_q(rel_path)}' }}")
        res = self._call(lambda: self._service.files().list(
            q=q, fields="files(id)", pageSize=10, spaces="drive").execute(num_retries=3))
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def create_folder(self, parent_id: str, name: str) -> str:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        res = self._call(lambda: self._service.files().create(
            body=body, fields="id").execute(num_retries=3))
        return res["id"]

    def create_file(self, parent_id: str, name: str, local_path: Path,
                    app_properties: dict[str, str]) -> UploadResult:
        body = {"name": name, "parents": [parent_id], "appProperties": app_properties}

        def go():
            req = self._service.files().create(
                body=body, media_body=self._media(local_path), fields="id,headRevisionId,trashed")
            return self._run(req)
        res = self._call(go)
        if res.get("trashed"):
            # created inside a folder the user trashed
            raise RemoteNotFoundError(f"parent {parent_id} is in the Drive trash")
        return UploadResult(res["id"], res.get("headRevisionId", ""))

    def update_file(self, file_id: str, local_path: Path,
                    app_properties: dict[str, str]) -> UploadResult:
        body = {"appProperties": app_properties}

        def go():
            req = self._service.files().update(
                fileId=file_id, body=body, media_body=self._media(local_path),
                fields="id,headRevisionId,trashed")
            return self._run(req)
        res = self._call(go)
        if res.get("trashed"):
            raise RemoteNotFoundError(f"{file_id} is in the Drive trash")
        return UploadResult(res["id"], res.get("headRevisionId", ""))

    def keep_revision(self, file_id: str, revision_id: str) -> None:
        try:
            self._call(lambda: self._service.revisions().update(
                fileId=file_id, revisionId=revision_id, body={"keepForever": True},
                fields="id").execute(num_retries=3))
        except (TransientDriveError, QuotaExceededError, AuthError, RemoteNotFoundError):
            raise
        except DriveError as e:
            # Drive rejects keepForever past 200 kept revisions with a 4xx.
            raise RevisionLimitError(str(e)) from e

    def list_tree(self, root_id: str) -> Iterator[RemoteFile]:
        stack = [root_id]
        while stack:
            parent = stack.pop()
            token = None
            while True:
                res = self._call(lambda: self._service.files().list(
                    q=f"'{_q(parent)}' in parents and trashed = false",
                    fields="nextPageToken, files(id,name,mimeType,appProperties)",
                    pageSize=1000, pageToken=token, spaces="drive").execute(num_retries=3))
                for f in res.get("files", []):
                    if f.get("mimeType") == FOLDER_MIME:
                        stack.append(f["id"])
                    else:
                        yield RemoteFile(f["id"], parent, f["name"], f.get("appProperties", {}))
                token = res.get("nextPageToken")
                if not token:
                    break

    def storage_quota(self) -> tuple[int, int | None]:
        res = self._call(lambda: self._service.about().get(
            fields="storageQuota(limit,usage)").execute(num_retries=3))
        q = res.get("storageQuota", {})
        limit = q.get("limit")
        return int(q.get("usage", 0)), (int(limit) if limit else None)

    def user_email(self) -> str | None:
        res = self._call(lambda: self._service.about().get(
            fields="user(emailAddress)").execute(num_retries=3))
        return res.get("user", {}).get("emailAddress")
