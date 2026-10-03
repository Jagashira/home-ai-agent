"""Apply deterministic Gmail labels from saved classifications."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from app.mail.classifier import ClassificationResponseError, parse_classification_json
from app.mail.gmail.auth import ACCOUNT_IDS
from app.mail.gmail.client import get_account_email, get_gmail_service
from app.mail.gmail.labels import (
    add_labels_to_message,
    create_user_label,
    get_message_label_ids,
    list_user_label_ids,
)
from app.mail.gmail.list_messages import MessageMetadata, fetch_recent_messages
from app.mail.label_policy import AI_LABEL_NAMES, labels_for_classification
from app.storage.classification_store import ClassificationStore


@dataclass
class LabelStats:
    messages_considered: int = 0
    would_modify: int = 0
    no_label_needed: int = 0
    missing_classification: int = 0
    modified: int = 0
    already_correct: int = 0
    failed: int = 0


def _format_labels(labels: set[str]) -> str:
    return ", ".join(sorted(labels)) if labels else "-"


def process_account_messages(
    service: Any,
    messages: list[MessageMetadata],
    store: ClassificationStore,
    *,
    apply: bool,
    stats: LabelStats,
    output_stream: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
) -> None:
    """Process one account while isolating failures to individual messages."""
    label_ids_by_name = list_user_label_ids(service)

    for metadata in messages:
        stats.messages_considered += 1
        try:
            saved_json = store.get_saved_classification(
                provider=metadata["provider"],
                account_id=metadata["account_id"],
                message_id=metadata["message_id"],
            )
            if saved_json is None:
                stats.missing_classification += 1
                continue

            classification = parse_classification_json(saved_json)
            desired_names = labels_for_classification(classification)
            current_ids = get_message_label_ids(service, metadata["message_id"])
            current_ai_names = {
                name
                for name, label_id in label_ids_by_name.items()
                if name in AI_LABEL_NAMES and label_id in current_ids
            }
            names_to_add = desired_names - current_ai_names

            print(
                f"{metadata['account_id']} | {metadata['subject']}",
                file=output_stream,
            )
            print(
                f"Current AI labels: {_format_labels(current_ai_names)}",
                file=output_stream,
            )

            if not names_to_add:
                if apply:
                    stats.already_correct += 1
                    print("Already correct", file=output_stream)
                else:
                    stats.no_label_needed += 1
                    print("Would add: -", file=output_stream)
                print(file=output_stream)
                continue

            if not apply:
                stats.would_modify += 1
                print("Would add:", file=output_stream)
                for name in sorted(names_to_add):
                    print(f"  {name}", file=output_stream)
                print(file=output_stream)
                continue

            for name in sorted(names_to_add):
                if name not in label_ids_by_name:
                    label_ids_by_name[name] = create_user_label(service, name)
            add_labels_to_message(
                service,
                metadata["message_id"],
                {label_ids_by_name[name] for name in names_to_add},
            )
            stats.modified += 1
            print("Added:", file=output_stream)
            for name in sorted(names_to_add):
                print(f"  {name}", file=output_stream)
            print(file=output_stream)
        except ClassificationResponseError:
            stats.failed += 1
            print(
                f"{metadata['account_id']} / message "
                f"{metadata['message_id']}: invalid saved classification",
                file=error_stream,
            )
        except Exception:
            stats.failed += 1
            print(
                f"{metadata['account_id']} / message "
                f"{metadata['message_id']}: label processing failed",
                file=error_stream,
            )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply deterministic Gmail labels from cached classifications."
    )
    parser.add_argument("--hours", type=_positive_int, default=24)
    parser.add_argument("--account", choices=ACCOUNT_IDS)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing AI labels and add them to messages (default: dry-run).",
    )
    return parser.parse_args()


def print_summary(stats: LabelStats, *, apply: bool) -> None:
    print(f"Messages considered: {stats.messages_considered}")
    if apply:
        print(f"Modified: {stats.modified}")
        print(f"Already correct: {stats.already_correct}")
    else:
        print(f"Would modify: {stats.would_modify}")
        print(f"No label needed: {stats.no_label_needed}")
    print(f"Missing classification: {stats.missing_classification}")
    print(f"Failed: {stats.failed}")


def main() -> int:
    args = parse_args()
    account_ids = (args.account,) if args.account else ACCOUNT_IDS
    stats = LabelStats()

    try:
        with ClassificationStore() as store:
            for account_id in account_ids:
                try:
                    service = get_gmail_service(account_id)
                    account_email = get_account_email(service)
                    messages = fetch_recent_messages(
                        service,
                        account_id,
                        account_email,
                        hours=args.hours,
                    )
                    process_account_messages(
                        service,
                        messages,
                        store,
                        apply=args.apply,
                        stats=stats,
                    )
                except Exception:
                    stats.failed += 1
                    print(f"{account_id}: account processing failed", file=sys.stderr)
    except Exception:
        print("SQLite storage initialization failed", file=sys.stderr)
        return 1

    print_summary(stats, apply=args.apply)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
