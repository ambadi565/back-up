import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from drive_backup.drive_client import (
    QuotaExceededError, RemoteNotFoundError, TransientDriveError, _map_http_error,
)


def _err(status, reason):
    resp = httplib2.Response({"status": status})
    body = json.dumps({"error": {"code": status, "errors": [{"reason": reason}], "message": reason}})
    return HttpError(resp, body.encode())


@pytest.mark.parametrize("status,reason,cls", [
    (403, "storageQuotaExceeded", QuotaExceededError),
    (429, "rateLimitExceeded", TransientDriveError),
    (404, "notFound", RemoteNotFoundError),
    (403, "userRateLimitExceeded", TransientDriveError),
    (500, "backendError", TransientDriveError),
])
def test_error_mapping(status, reason, cls):
    assert isinstance(_map_http_error(_err(status, reason)), cls)


def test_service_object_per_thread(monkeypatch):
    import threading

    import googleapiclient.discovery

    from drive_backup.drive_client import GoogleDriveClient
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **kw: object())
    client = GoogleDriveClient(creds=None)
    seen = []
    t = threading.Thread(target=lambda: seen.append(client._service))
    t.start()
    t.join()
    assert seen[0] is not client._service
    assert client._service is client._service
