"""List recent Gmail message metadata for all configured accounts."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from app.mail.gmail.auth import ACCOUNT_IDS
from app.mail.gmail.client import get_account_email, get_gmail_service


MAX_MESSAGES_PER_ACCOUNT = 50
TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")


MessageMetadata = TypedDict(
    "MessageMetadata",
    {
        "account_id": str,
        "provider": str,
        "account_email": str,
        "message_id": str,
        "thread_id": str,
        "from": str,
        "subject": str,
        "received_at": datetime,
    },
)


def normalize_message(
    account_id: str,
    account_email: str,
    message: dict[str, Any],
) -> MessageMetadata:
    """Normalize a Gmail metadata response without reading message content."""
    headers = {
        header.get("name", "").lower(): header.get("value", "")
        for header in message.get("payload", {}).get("headers", [])
    }
    received_at = datetime.fromtimestamp(
        int(message["internalDate"]) / 1000,
        tz=timezone.utc,
    )

    return {
        "account_id": account_id,
        "provider": "gmail",
        "account_email": account_email,
        "message_id": str(message["id"]),
        "thread_id": str(message["threadId"]),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "received_at": received_at,
    }


def fetch_recent_messages(
    service: Any,
    account_id: str,
    account_email: str,
    now: datetime | None = None,
    hours: int = 24,
) -> list[MessageMetadata]:
    """Fetch up to 50 INBOX messages received during the requested period."""
    if hours <= 0:
        raise ValueError("hours must be greater than zero")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff_time = current_time.astimezone(timezone.utc) - timedelta(hours=hours)
    cutoff_epoch = int(cutoff_time.timestamp())

    response = (
        service.users()
        .messages()
        .list(
            userId="me",
            labelIds=["INBOX"],
            q=f"after:{cutoff_epoch} -in:spam -in:trash",
            includeSpamTrash=False,
            maxResults=MAX_MESSAGES_PER_ACCOUNT,
            fields="messages/id,messages/threadId",
        )
        .execute()
    )

    messages: list[MessageMetadata] = []
    for message_reference in response.get("messages", []):
        message = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_reference["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
                fields="id,threadId,internalDate,payload/headers",
            )
            .execute()
        )
        messages.append(normalize_message(account_id, account_email, message))

    return sorted(messages, key=lambda item: item["received_at"], reverse=True)


def print_messages(
    account_id: str,
    account_email: str,
    messages: list[MessageMetadata],
) -> None:
    """Print one account's messages in the required terminal format."""
    print(f"Account: {account_id}")
    print(f"Email: {account_email}")
    print()

    for message in messages:
        received_at = message["received_at"].astimezone(TOKYO_TIMEZONE)
        print(f"[{received_at:%Y-%m-%d %H:%M}]")
        print(f"From: {message['from']}")
        print(f"Subject: {message['subject']}")
        print(f"Message ID: {message['message_id']}")
        print(f"Thread ID: {message['thread_id']}")
        print()


def main() -> None:
    for account_id in ACCOUNT_IDS:
        try:
            service = get_gmail_service(account_id)
            account_email = get_account_email(service)
        except (FileNotFoundError, ValueError, GoogleAuthError):
            print(f"{account_id}: authentication failed", file=sys.stderr)
            continue
        except HttpError:
            print(f"{account_id}: profile request failed", file=sys.stderr)
            continue
        except Exception:
            # Do not expose exception details that may contain authentication data.
            print(f"{account_id}: authentication failed", file=sys.stderr)
            continue

        try:
            messages = fetch_recent_messages(service, account_id, account_email)
            print_messages(account_id, account_email, messages)
        except (HttpError, KeyError, TypeError, ValueError):
            print(f"{account_id}: message metadata request failed", file=sys.stderr)
        except Exception:
            # Keep failures isolated so the remaining accounts are still processed.
            print(f"{account_id}: message metadata request failed", file=sys.stderr)


if __name__ == "__main__":
    main()
