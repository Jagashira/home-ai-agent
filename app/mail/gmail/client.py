"""Gmail API client creation using previously authorized accounts."""

from __future__ import annotations

from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.mail.gmail.auth import SCOPES, save_credentials, token_path_for


def load_stored_credentials(account_id: str) -> Credentials:
    """Load and, when necessary, refresh an account's stored credentials."""
    token_path = token_path_for(account_id)
    if not token_path.is_file():
        raise FileNotFoundError(f"OAuth token not found for {account_id}")

    credentials = Credentials.from_authorized_user_file(token_path, SCOPES)

    if credentials.expired:
        if not credentials.refresh_token:
            raise RefreshError("Stored OAuth token cannot be refreshed")
        credentials.refresh(Request())
        save_credentials(credentials, token_path)

    if not credentials.valid:
        raise RefreshError("Stored OAuth token is not valid")

    return credentials


def get_gmail_service(account_id: str) -> Any:
    """Return an authenticated Gmail API service for a supported account."""
    credentials = load_stored_credentials(account_id)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def get_account_email(service: Any) -> str:
    """Return the authenticated Gmail account's email address."""
    profile = service.users().getProfile(userId="me").execute()
    return str(profile["emailAddress"])
