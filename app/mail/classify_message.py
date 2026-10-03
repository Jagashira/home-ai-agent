"""Fetch and classify one Gmail message with DeepSeek."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from openai import OpenAIError

from app.ai.deepseek_client import (
    DeepSeekConfigurationError,
    format_deepseek_api_error,
    get_deepseek_client,
)
from app.mail.body_normalizer import normalize_email_body
from app.mail.classifier import ClassificationResponseError, classify_email
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
        description="Classify one Gmail message with DeepSeek."
    )
    parser.add_argument("account_id", choices=ACCOUNT_IDS)
    parser.add_argument("message_id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        service = get_gmail_service(args.account_id)
        message = get_full_message(service, args.message_id)
        payload = message.get("payload", {})
        sender = _safe_header_value(payload, "From")
        subject = _safe_header_value(payload, "Subject")
        received_at = _received_at(message)
        normalized_body = normalize_email_body(extract_message_body(payload))
    except (FileNotFoundError, GoogleAuthError):
        print(f"{args.account_id}: authentication failed", file=sys.stderr)
        return 1
    except (HttpError, KeyError, TypeError, ValueError):
        print(f"{args.account_id}: message request failed", file=sys.stderr)
        return 1
    except Exception:
        print(f"{args.account_id}: message request failed", file=sys.stderr)
        return 1

    try:
        client = get_deepseek_client()
        classification = classify_email(
            client,
            received_at=f"{received_at:%Y-%m-%d %H:%M:%S} Asia/Tokyo",
            sender=sender,
            subject=subject,
            normalized_body=normalized_body,
        )
    except DeepSeekConfigurationError:
        print("DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 1
    except ClassificationResponseError:
        print("DeepSeek returned an invalid classification response", file=sys.stderr)
        return 1
    except OpenAIError as error:
        print(format_deepseek_api_error(error), file=sys.stderr)
        return 1
    except Exception:
        print("DeepSeek API request failed", file=sys.stderr)
        return 1

    print(
        json.dumps(
            classification.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
