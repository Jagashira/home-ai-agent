"""Authenticate one of the configured Google accounts for Gmail access."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
ACCOUNT_IDS = tuple(f"google_{number}" for number in range(1, 5))
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CREDENTIALS_PATH = PROJECT_ROOT / "secrets" / "google" / "credentials.json"


def token_path_for(account_id: str) -> Path:
    """Return the token path for a supported account ID."""
    if account_id not in ACCOUNT_IDS:
        raise ValueError(f"Unsupported account ID: {account_id}")
    return CREDENTIALS_PATH.parent / f"token_{account_id}.json"


def save_credentials(credentials: Credentials, token_path: Path) -> None:
    """Save OAuth credentials with permissions limited to the current user."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(token_path, 0o600)


def authenticate(account_id: str) -> tuple[str, Path]:
    """Authenticate an account and verify the connection with Gmail."""
    token_path = token_path_for(account_id)
    credentials: Credentials | None = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(token_path, SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            save_credentials(credentials, token_path)
        except RefreshError:
            credentials = None

    if not credentials or not credentials.valid:
        if not CREDENTIALS_PATH.is_file():
            raise FileNotFoundError(
                f"Google OAuth credentials file not found: {CREDENTIALS_PATH}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        credentials = flow.run_local_server(
            port=0,
            authorization_prompt_message=None,
        )
        save_credentials(credentials, token_path)

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"], token_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authenticate a configured Google account for Gmail."
    )
    parser.add_argument("account_id", choices=ACCOUNT_IDS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    email_address, token_path = authenticate(args.account_id)
    print(f"Google account: {email_address}")
    print(f"Token path: {token_path}")


if __name__ == "__main__":
    main()
