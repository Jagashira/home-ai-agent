"""Tests for recent-message batch classification."""

from __future__ import annotations

import base64
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from app.ai.deepseek_client import DEEPSEEK_MODEL
from app.mail.classifier import (
    CLASSIFIER_VERSION,
    EmailClassification,
    format_received_at,
)
from app.mail.classify_recent import (
    BatchStats,
    ClassifiedMessage,
    RecentMailClassifier,
    calculate_content_hash,
    parse_args,
    print_results,
    process_messages,
)
from app.mail.gmail.list_messages import MessageMetadata, fetch_recent_messages
from app.storage.classification_store import ClassificationStore


def message_metadata(number: int) -> MessageMetadata:
    return {
        "account_id": "google_1",
        "provider": "gmail",
        "account_email": "one@example.com",
        "message_id": f"message-{number}",
        "thread_id": f"thread-{number}",
        "from": "sender@example.com",
        "subject": f"Subject {number}",
        "received_at": datetime(2026, 10, 4, number, tzinfo=timezone.utc),
    }


def full_message(body: str) -> dict[str, object]:
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Content-Type", "value": "text/plain; charset=utf-8"}
            ],
            "body": {"data": encoded},
        }
    }


def classification(summary: str = "分類結果です。") -> EmailClassification:
    return EmailClassification.model_validate(
        {
            "domain": "service",
            "sender_type": "direct_organization",
            "mail_type": "information",
            "organization": "Example Service",
            "importance": 2,
            "action_required": False,
            "reply_required": False,
            "deadline_at": None,
            "event_at": None,
            "summary": summary,
            "confidence": 0.9,
        }
    )


class ClassifyRecentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "mail.db"
        self.store = ClassificationStore(database_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _save_cache(
        self,
        metadata: MessageMetadata,
        body: str,
        *,
        model: str = DEEPSEEK_MODEL,
        classifier_version: str = CLASSIFIER_VERSION,
    ) -> None:
        received_at = format_received_at(metadata["received_at"])
        content_hash = calculate_content_hash(
            received_at=received_at,
            sender=metadata["from"],
            subject=metadata["subject"],
            normalized_body=body,
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
            classification_json=classification().model_dump_json(),
            model=model,
            classifier_version=classifier_version,
        )

    def test_cache_hit_does_not_create_client_or_call_deepseek(self) -> None:
        metadata = message_metadata(1)
        body = "Cached body"
        self._save_cache(metadata, body)
        client_factory = Mock(side_effect=AssertionError("must not be called"))
        processor = RecentMailClassifier(
            self.store,
            client_factory=client_factory,
        )

        with patch(
            "app.mail.classify_recent.get_full_message",
            return_value=full_message(body),
        ):
            outcome = processor.process(object(), metadata)

        self.assertEqual(outcome.status, "cache")
        client_factory.assert_not_called()

    def test_force_ignores_cache_and_reclassifies(self) -> None:
        metadata = message_metadata(1)
        body = "Cached body"
        self._save_cache(metadata, body)
        processor = RecentMailClassifier(
            self.store,
            force=True,
            client_factory=lambda: object(),
        )

        with (
            patch(
                "app.mail.classify_recent.get_full_message",
                return_value=full_message(body),
            ),
            patch(
                "app.mail.classify_recent.classify_email",
                return_value=classification("新しい分類です。"),
            ) as classify_mock,
        ):
            outcome = processor.process(object(), metadata)

        self.assertEqual(outcome.status, "new")
        classify_mock.assert_called_once()

    def test_max_new_limits_deepseek_calls_globally(self) -> None:
        messages = [message_metadata(number) for number in range(1, 8)]
        self._save_cache(messages[0], "message-1")
        processor = RecentMailClassifier(
            self.store,
            max_new=5,
            client_factory=lambda: object(),
        )
        stats = BatchStats(messages_found=len(messages))

        with (
            patch(
                "app.mail.classify_recent.get_full_message",
                side_effect=lambda service, message_id: full_message(message_id),
            ),
            patch(
                "app.mail.classify_recent.classify_email",
                return_value=classification(),
            ) as classify_mock,
        ):
            results = process_messages(
                processor,
                object(),
                messages,
                stats,
                error_stream=StringIO(),
            )

        self.assertEqual(classify_mock.call_count, 5)
        self.assertEqual(stats.cache_hits, 1)
        self.assertEqual(stats.new_classifications, 5)
        self.assertEqual(stats.skipped_by_max_new, 1)
        self.assertEqual(len(results), 6)

    def test_failure_does_not_stop_later_messages(self) -> None:
        messages = [message_metadata(1), message_metadata(2)]
        processor = RecentMailClassifier(
            self.store,
            client_factory=lambda: object(),
        )
        stats = BatchStats(messages_found=2)

        def fetch_message(service: object, message_id: str) -> dict[str, object]:
            if message_id == "message-1":
                raise RuntimeError("simulated private failure")
            return full_message("Second body")

        errors = StringIO()
        with (
            patch(
                "app.mail.classify_recent.get_full_message",
                side_effect=fetch_message,
            ),
            patch(
                "app.mail.classify_recent.classify_email",
                return_value=classification(),
            ) as classify_mock,
        ):
            results = process_messages(
                processor,
                object(),
                messages,
                stats,
                error_stream=errors,
            )

        self.assertEqual(stats.failed, 1)
        self.assertEqual(stats.new_classifications, 1)
        self.assertEqual(len(results), 1)
        classify_mock.assert_called_once()
        self.assertNotIn("simulated private failure", errors.getvalue())

    def test_cli_options_are_parsed(self) -> None:
        with patch(
            "sys.argv",
            [
                "classify_recent",
                "--hours",
                "12",
                "--account",
                "google_2",
                "--max-new",
                "5",
                "--force",
            ],
        ):
            args = parse_args()

        self.assertEqual(args.hours, 12)
        self.assertEqual(args.account, "google_2")
        self.assertEqual(args.max_new, 5)
        self.assertTrue(args.force)

    def test_hours_controls_gmail_search_cutoff(self) -> None:
        request = Mock()
        request.execute.return_value = {}
        messages_resource = Mock()
        messages_resource.list.return_value = request
        users_resource = Mock()
        users_resource.messages.return_value = messages_resource
        service = Mock()
        service.users.return_value = users_resource

        fetch_recent_messages(
            service,
            "google_1",
            "one@example.com",
            now=datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc),
            hours=12,
        )

        self.assertEqual(
            messages_resource.list.call_args.kwargs["q"],
            "after:1791028800 -in:spam -in:trash",
        )

    def test_results_are_sorted_by_importance_then_received_time(self) -> None:
        low = classification().model_copy(update={"importance": 1})
        high_old = classification().model_copy(update={"importance": 4})
        high_new = classification().model_copy(update={"importance": 4})
        results = [
            ClassifiedMessage(message_metadata(3), low, "cache"),
            ClassifiedMessage(message_metadata(1), high_old, "cache"),
            ClassifiedMessage(message_metadata(2), high_new, "DeepSeek"),
        ]
        output = StringIO()

        with redirect_stdout(output):
            print_results(results)

        rendered = output.getvalue()
        self.assertLess(
            rendered.index("Subject: Subject 2"),
            rendered.index("Subject: Subject 1"),
        )
        self.assertLess(
            rendered.index("Subject: Subject 1"),
            rendered.index("Subject: Subject 3"),
        )


if __name__ == "__main__":
    unittest.main()
