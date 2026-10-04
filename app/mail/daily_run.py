"""Run the recent-mail classification, labeling, and digest pipeline."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TextIO

from app.mail.apply_labels import GmailLabelApplier
from app.mail.classify_recent import (
    BatchStats,
    ClassifiedMessage,
    RecentMailClassifier,
    process_messages,
)
from app.mail.digest import generate_daily_digest
from app.mail.gmail.auth import ACCOUNT_IDS
from app.mail.gmail.client import get_account_email, get_gmail_service
from app.mail.gmail.list_messages import MessageMetadata, fetch_recent_messages
from app.mail.home_assistant_actions import (
    HomeAssistantActionStats,
    process_home_assistant_actions,
)
from app.storage.classification_store import ClassificationStore


@dataclass
class DailyRunStats:
    accounts: int = 0
    messages_found: int = 0
    cache_hits: int = 0
    new_classifications: int = 0
    skipped_by_max_new: int = 0
    labels_added: int = 0
    would_add_labels: int = 0
    already_labeled: int = 0
    classification_failures: int = 0
    label_failures: int = 0
    account_failures: int = 0
    home_assistant: HomeAssistantActionStats = field(
        default_factory=HomeAssistantActionStats
    )


def process_account(
    service: Any,
    messages: list[MessageMetadata],
    processor: RecentMailClassifier,
    stats: DailyRunStats,
    *,
    dry_run: bool,
    error_stream: TextIO = sys.stderr,
) -> list[ClassifiedMessage]:
    """Classify and label one account while keeping failures isolated."""
    classification_stats = BatchStats(messages_found=len(messages))
    classified = process_messages(
        processor,
        service,
        messages,
        classification_stats,
        check_current_cache_first=True,
        error_stream=error_stream,
    )
    stats.cache_hits += classification_stats.cache_hits
    stats.new_classifications += classification_stats.new_classifications
    stats.skipped_by_max_new += classification_stats.skipped_by_max_new
    stats.classification_failures += classification_stats.failed

    if not classified:
        return classified

    try:
        label_applier = GmailLabelApplier(service)
    except Exception:
        stats.label_failures += len(classified)
        print(
            f"{messages[0]['account_id']}: Gmail label lookup failed",
            file=error_stream,
        )
        return classified

    for item in classified:
        try:
            result = label_applier.process(
                item.metadata["message_id"],
                item.classification,
                apply=not dry_run,
            )
            if result.names_to_add:
                if dry_run:
                    stats.would_add_labels += len(result.names_to_add)
                else:
                    stats.labels_added += len(result.names_to_add)
            else:
                stats.already_labeled += 1
        except Exception:
            stats.label_failures += 1
            print(
                f"{item.metadata['account_id']} / message "
                f"{item.metadata['message_id']}: label processing failed",
                file=error_stream,
            )
    return classified


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify, label, and summarize recent Gmail messages."
    )
    parser.add_argument("--hours", type=_positive_int, default=24)
    parser.add_argument("--account", choices=ACCOUNT_IDS)
    parser.add_argument("--max-new", type=_nonnegative_int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Classify and build the digest without changing Gmail labels or "
            "calling Home Assistant."
        ),
    )
    return parser.parse_args()


def print_pipeline_summary(stats: DailyRunStats, *, dry_run: bool) -> None:
    print(f"Accounts: {stats.accounts}")
    print(f"Messages found: {stats.messages_found}")
    print(f"Cache hits: {stats.cache_hits}")
    print(f"New classifications: {stats.new_classifications}")
    print(f"Label assignments added: {stats.labels_added}")
    if dry_run:
        print(f"Would add label assignments: {stats.would_add_labels}")
    print(f"Already labeled: {stats.already_labeled}")
    print(f"Classification failures: {stats.classification_failures}")
    print(f"Label failures: {stats.label_failures}")
    print(f"Home Assistant tasks added: {stats.home_assistant.tasks_added}")
    if dry_run:
        print(
            "Would add Home Assistant tasks: "
            f"{stats.home_assistant.would_add_tasks}"
        )
    print(
        "Home Assistant tasks already present: "
        f"{stats.home_assistant.tasks_already_present}"
    )
    print(f"Notifications sent: {stats.home_assistant.notifications_sent}")
    if dry_run:
        print(
            "Would send notifications: "
            f"{stats.home_assistant.would_send_notifications}"
        )
    print(
        "Notifications already sent: "
        f"{stats.home_assistant.notifications_already_sent}"
    )
    print(f"Home Assistant failures: {stats.home_assistant.failures}")
    if stats.skipped_by_max_new:
        print(f"Skipped by --max-new: {stats.skipped_by_max_new}")
    if stats.account_failures:
        print(f"Account failures: {stats.account_failures}")


def main() -> int:
    args = parse_args()
    account_ids = (args.account,) if args.account else ACCOUNT_IDS
    stats = DailyRunStats(accounts=len(account_ids))
    results: list[ClassifiedMessage] = []
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(hours=args.hours)

    try:
        with ClassificationStore() as store:
            processor = RecentMailClassifier(store, max_new=args.max_new)
            for account_id in account_ids:
                try:
                    service = get_gmail_service(account_id)
                    account_email = get_account_email(service)
                    messages = fetch_recent_messages(
                        service,
                        account_id,
                        account_email,
                        now=period_end,
                        hours=args.hours,
                    )
                except Exception:
                    stats.account_failures += 1
                    print(f"{account_id}: account processing failed", file=sys.stderr)
                    continue

                stats.messages_found += len(messages)
                try:
                    results.extend(
                        process_account(
                            service,
                            messages,
                            processor,
                            stats,
                            dry_run=args.dry_run,
                        )
                    )
                except Exception:
                    stats.account_failures += 1
                    print(f"{account_id}: account processing failed", file=sys.stderr)
            process_home_assistant_actions(
                results,
                store,
                dry_run=args.dry_run,
                stats=stats.home_assistant,
            )
    except Exception:
        print("SQLite storage initialization failed", file=sys.stderr)
        return 1

    print_pipeline_summary(stats, dry_run=args.dry_run)
    print()
    print(
        generate_daily_digest(
            results,
            period_start=period_start,
            period_end=period_end,
            accounts=stats.accounts,
            messages_found=stats.messages_found,
            new_classifications=stats.new_classifications,
            cache_hits=stats.cache_hits,
        )
    )
    return 1 if (
        stats.account_failures
        or stats.classification_failures
        or stats.label_failures
        or stats.home_assistant.failures
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
