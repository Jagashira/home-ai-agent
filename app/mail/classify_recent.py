"""Classify recent Gmail messages sequentially with a local SQLite cache."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from typing import Any, Callable, Literal, TextIO

from app.ai.deepseek_client import DEEPSEEK_MODEL, get_deepseek_client
from app.mail.body_normalizer import normalize_email_body
from app.mail.classifier import (
    CLASSIFIER_VERSION,
    ClassificationResponseError,
    EmailClassification,
    build_classification_input,
    classify_email,
    format_received_at,
    parse_classification_json,
)
from app.mail.gmail.auth import ACCOUNT_IDS
from app.mail.gmail.body import extract_message_body, get_full_message
from app.mail.gmail.client import get_account_email, get_gmail_service
from app.mail.gmail.list_messages import (
    TOKYO_TIMEZONE,
    MessageMetadata,
    fetch_recent_messages,
)
from app.storage.classification_store import ClassificationStore


Source = Literal["cache", "DeepSeek"]
Status = Literal["cache", "new", "skipped"]


@dataclass(frozen=True)
class ClassifiedMessage:
    metadata: MessageMetadata
    classification: EmailClassification
    source: Source


@dataclass(frozen=True)
class ProcessingOutcome:
    status: Status
    result: ClassifiedMessage | None = None


@dataclass
class BatchStats:
    accounts: int = 0
    messages_found: int = 0
    cache_hits: int = 0
    new_classifications: int = 0
    skipped_by_max_new: int = 0
    failed: int = 0


def calculate_content_hash(
    *,
    received_at: str,
    sender: str,
    subject: str,
    normalized_body: str,
) -> str:
    """Hash exactly the four email fields sent to DeepSeek."""
    classifier_input = build_classification_input(
        received_at=received_at,
        sender=sender,
        subject=subject,
        normalized_body=normalized_body,
    )
    return hashlib.sha256(classifier_input.encode("utf-8")).hexdigest()


class RecentMailClassifier:
    """Classify messages sequentially while applying cache and API-call limits."""

    def __init__(
        self,
        store: ClassificationStore,
        *,
        force: bool = False,
        max_new: int | None = None,
        client_factory: Callable[[], Any] = get_deepseek_client,
    ) -> None:
        self.store = store
        self.force = force
        self.max_new = max_new
        self.client_factory = client_factory
        self.model = DEEPSEEK_MODEL
        self.classifier_version = CLASSIFIER_VERSION
        self._client: Any | None = None
        self._api_calls = 0

    def get_current_result(
        self,
        metadata: MessageMetadata,
    ) -> ClassifiedMessage | None:
        """Read a current cached result before fetching immutable Gmail content."""
        if self.force:
            return None
        cached_json = self.store.get_current_classification(
            provider=metadata["provider"],
            account_id=metadata["account_id"],
            message_id=metadata["message_id"],
            model=self.model,
            classifier_version=self.classifier_version,
        )
        if cached_json is None:
            return None
        try:
            classification = parse_classification_json(cached_json)
        except ClassificationResponseError:
            return None
        return ClassifiedMessage(metadata, classification, "cache")

    def process(self, service: Any, metadata: MessageMetadata) -> ProcessingOutcome:
        """Process one message without logging or persisting its body."""
        message = get_full_message(service, metadata["message_id"])
        normalized_body = normalize_email_body(
            extract_message_body(message.get("payload", {}))
        )
        received_at = format_received_at(metadata["received_at"])
        content_hash = calculate_content_hash(
            received_at=received_at,
            sender=metadata["from"],
            subject=metadata["subject"],
            normalized_body=normalized_body,
        )

        if not self.force:
            cached_json = self.store.get_cached_classification(
                provider=metadata["provider"],
                account_id=metadata["account_id"],
                message_id=metadata["message_id"],
                content_hash=content_hash,
                model=self.model,
                classifier_version=self.classifier_version,
            )
            if cached_json is not None:
                try:
                    classification = parse_classification_json(cached_json)
                except ClassificationResponseError:
                    classification = None
                if classification is not None:
                    return ProcessingOutcome(
                        status="cache",
                        result=ClassifiedMessage(metadata, classification, "cache"),
                    )

        if self.max_new is not None and self._api_calls >= self.max_new:
            return ProcessingOutcome(status="skipped")

        if self._client is None:
            self._client = self.client_factory()
        self._api_calls += 1
        classification = classify_email(
            self._client,
            received_at=received_at,
            sender=metadata["from"],
            subject=metadata["subject"],
            normalized_body=normalized_body,
        )
        self.store.save_classification(
            provider=metadata["provider"],
            account_id=metadata["account_id"],
            message_id=metadata["message_id"],
            thread_id=metadata["thread_id"],
            from_address=metadata["from"],
            subject=metadata["subject"],
            received_at=metadata["received_at"].isoformat(),
            content_hash=content_hash,
            classification_json=classification.model_dump_json(),
            model=self.model,
            classifier_version=self.classifier_version,
        )
        return ProcessingOutcome(
            status="new",
            result=ClassifiedMessage(metadata, classification, "DeepSeek"),
        )


def process_messages(
    processor: RecentMailClassifier,
    service: Any,
    messages: list[MessageMetadata],
    stats: BatchStats,
    *,
    check_current_cache_first: bool = False,
    error_stream: TextIO = sys.stderr,
) -> list[ClassifiedMessage]:
    """Process messages independently so one failure does not stop the sequence."""
    results: list[ClassifiedMessage] = []
    for metadata in messages:
        try:
            current_result = (
                processor.get_current_result(metadata)
                if check_current_cache_first
                else None
            )
            if current_result is not None:
                outcome = ProcessingOutcome(status="cache", result=current_result)
            else:
                outcome = processor.process(service, metadata)
        except Exception:
            stats.failed += 1
            print(
                f"{metadata['account_id']} / message "
                f"{metadata['message_id']}: classification failed",
                file=error_stream,
            )
            continue

        if outcome.status == "cache":
            stats.cache_hits += 1
        elif outcome.status == "new":
            stats.new_classifications += 1
        else:
            stats.skipped_by_max_new += 1

        if outcome.result is not None:
            results.append(outcome.result)
    return results


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
        description="Classify recent Gmail messages with a local cache."
    )
    parser.add_argument("--hours", type=_positive_int, default=24)
    parser.add_argument("--account", choices=ACCOUNT_IDS)
    parser.add_argument("--max-new", type=_nonnegative_int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def print_summary(stats: BatchStats) -> None:
    print(f"Accounts: {stats.accounts}")
    print(f"Messages found: {stats.messages_found}")
    print(f"Cache hits: {stats.cache_hits}")
    print(f"New classifications: {stats.new_classifications}")
    print(f"Skipped by --max-new: {stats.skipped_by_max_new}")
    print(f"Failed: {stats.failed}")


def print_results(results: list[ClassifiedMessage]) -> None:
    ordered = sorted(
        results,
        key=lambda item: (
            item.classification.importance,
            item.metadata["received_at"],
        ),
        reverse=True,
    )
    for item in ordered:
        metadata = item.metadata
        classification = item.classification
        serialized = classification.model_dump(mode="json")
        received_at = metadata["received_at"].astimezone(TOKYO_TIMEZONE)
        print()
        print(f"[Importance {classification.importance}]")
        print(f"{metadata['account_id']} | {received_at:%Y-%m-%d %H:%M}")
        print(f"From: {metadata['from']}")
        print(f"Subject: {metadata['subject']}")
        print(
            "Type: "
            f"{classification.domain} / {classification.sender_type} / "
            f"{classification.mail_type}"
        )
        print(f"Action required: {'yes' if classification.action_required else 'no'}")
        print(f"Reply required: {'yes' if classification.reply_required else 'no'}")
        print(f"Deadline: {serialized['deadline_at'] or '-'}")
        print(f"Summary: {classification.summary}")
        print(f"Source: {item.source}")


def main() -> int:
    args = parse_args()
    account_ids = (args.account,) if args.account else ACCOUNT_IDS
    stats = BatchStats(accounts=len(account_ids))
    results: list[ClassifiedMessage] = []

    try:
        with ClassificationStore() as store:
            processor = RecentMailClassifier(
                store,
                force=args.force,
                max_new=args.max_new,
            )
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
                except Exception:
                    stats.failed += 1
                    print(f"{account_id}: account processing failed", file=sys.stderr)
                    continue

                stats.messages_found += len(messages)
                results.extend(process_messages(processor, service, messages, stats))
    except Exception:
        print("SQLite storage initialization failed", file=sys.stderr)
        return 1

    print_summary(stats)
    print_results(results)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
