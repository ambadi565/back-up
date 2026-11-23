import datetime
import os
import sys

import pytest
from google.oauth2.credentials import Credentials

from drive_backup.auth import load_credentials, save_token, sign_in


def _creds():
    return Credentials(token="t", refresh_token="r", client_id="c", client_secret="s",
                       token_uri="https://oauth2.googleapis.com/token",
                       expiry=datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                       + datetime.timedelta(hours=1))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_save_token_permissions(tmp_path):
    p = tmp_path / "token.json"
    save_token(_creds(), p)
    assert (os.stat(p).st_mode & 0o777) == 0o600


def test_load_missing_returns_none(tmp_path):
    assert load_credentials(tmp_path / "token.json") is None


def test_load_saved_token(tmp_path):
    p = tmp_path / "token.json"
    save_token(_creds(), p)
    creds = load_credentials(p)
    assert creds.refresh_token == "r"


def test_missing_client_secret_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="Google Cloud setup"):
        sign_in(tmp_path / "client_secret.json", tmp_path / "token.json")
