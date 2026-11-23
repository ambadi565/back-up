"""Google sign-in (OAuth installed-app flow) and token storage."""
from __future__ import annotations

import os
from pathlib import Path

from .drive_client import AuthError

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def save_token(creds, token_path: Path) -> None:
    token_path = Path(token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = token_path.with_suffix(".tmp")
    # create with restrictive permissions from the start
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.replace(tmp, token_path)
    if os.name == "posix":
        os.chmod(token_path, 0o600)


def load_credentials(token_path: Path):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(token_path)
    if not token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(f"Google sign-in expired or was revoked: {e}") from e
        save_token(creds, token_path)
    return creds


def sign_in(client_secret_path: Path, token_path: Path):
    client_secret_path = Path(client_secret_path)
    if not client_secret_path.exists():
        raise FileNotFoundError(
            f"{client_secret_path} not found. Follow the 'Google Cloud setup' section of the "
            f"README to create it.")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True,
                                  authorization_prompt_message="",
                                  success_message="Signed in. You can close this tab.")
    save_token(creds, token_path)
    return creds


def signed_in_email(creds) -> str | None:
    from .drive_client import GoogleDriveClient
    try:
        return GoogleDriveClient(creds).user_email()
    except Exception:
        return None
