"""Display one Gmail message's headers and extracted plain-text body."""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from app.mail.body_normalizer import normalize_email_body
from app.mail.gmail.auth import ACCOUNT_IDS
from app.mail.gmail.body import extract_message_body, get_full_message
from app.mail.gmail.client import get_gmail_service


TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")


def _safe_header_value(payload: dict[str, Any], name: str) -> str:
    value = ""
    for header in payload.get("headers", []):
        if str(header.get("name", "")).lower() == name.lower():
            value = str(header.get("value", ""))
            break
    value = "".join(
        character for character in value if unicodedata.category(character) != "Cc"
    )
    return " ".join(value.split())


def _received_at(message: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(
        int(message["internalDate"]) / 1000,
        tz=timezone.utc,
    ).astimezone(TOKYO_TIMEZONE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show the plain-text body of one Gmail message."
    )
    parser.add_argument("account_id", choices=ACCOUNT_IDS)
    parser.add_argument("message_id")
    parser.add_argument(
        "--normalized",
        action="store_true",
        help="Normalize the extracted body for AI input before displaying it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        service = get_gmail_service(args.account_id)
    except (FileNotFoundError, ValueError, GoogleAuthError):
        print(f"{args.account_id}: authentication failed", file=sys.stderr)
        return 1
    except Exception:
        print(f"{args.account_id}: authentication failed", file=sys.stderr)
        return 1

    try:
        message = get_full_message(service, args.message_id)
        payload = message.get("payload", {})
        sender = _safe_header_value(payload, "From")
        subject = _safe_header_value(payload, "Subject")
        received_at = _received_at(message)
        body = extract_message_body(payload)
        if args.normalized:
            body = normalize_email_body(body)
    except (HttpError, KeyError, TypeError, ValueError):
        print(f"{args.account_id}: message request failed", file=sys.stderr)
        return 1
    except Exception:
        print(f"{args.account_id}: message request failed", file=sys.stderr)
        return 1

    print(f"Account: {args.account_id}")
    print(f"From: {sender}")
    print(f"Subject: {subject}")
    print(f"Received: {received_at:%Y-%m-%d %H:%M}")
    print()
    print("Body:")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
