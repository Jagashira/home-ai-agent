from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from app.mail.classifier import EmailClassification
from app.mail.classify_recent import ClassifiedMessage
from app.mail.gmail.list_messages import MessageMetadata
from app.mail.morning_digest import (
    MorningDigestNotificationStats,
    format_morning_digest,
    process_morning_digest_notification,
)
from app.storage.classification_store import ClassificationStore


NOW = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)


def item(
    number: int,
    *,
    domain: str = "service",
    importance: int = 2,
    action: bool = False,
    reply: bool = False,
    deadline: str | None = None,
    mail_type: str = "information",
    organization: str | None = None,
    subject: str | None = None,
) -> ClassifiedMessage:
    metadata: MessageMetadata = {
        "account_id": "google_1",
        "provider": "gmail",
        "account_email": "user@example.com",
        "message_id": f"message-{number}",
        "thread_id": f"thread-{number}",
        "from": "sender@example.com",
        "subject": subject or f"Subject {number}",
        "received_at": NOW - timedelta(minutes=number),
    }
    classification = EmailClassification.model_validate(
        {
            "domain": domain,
            "sender_type": "direct_organization",
            "mail_type": mail_type,
            "organization": organization or f"Org {number}",
            "importance": importance,
            "action_required": action,
            "reply_required": reply,
            "deadline_at": deadline,
            "event_at": None,
            "summary": "This summary must not be copied to the phone.",
            "confidence": 0.9,
        }
    )
    return ClassifiedMessage(metadata, classification, "cache")


class MorningDigestFormatterTests(unittest.TestCase):
    def test_actionable_deadlines_are_first_and_sorted(self) -> None:
        messages = [
            item(1, action=True, deadline="2026-10-09T12:00:00+09:00"),
            item(2, action=True),
            item(3, reply=True, deadline="2026-10-09T10:00:00+09:00"),
        ]
        digest = format_morning_digest(messages, messages_found=3, now=NOW)
        self.assertLess(digest.body.index("10/09 10:00"), digest.body.index("10/09 12:00"))
        self.assertLess(digest.body.index("10/09 12:00"), digest.body.index("Subject 2"))

    def test_category_counts_are_included(self) -> None:
        messages = [
            item(1, domain="job"),
            item(2, domain="university_research"),
            item(3, domain="purchase_billing"),
            item(4, domain="security", importance=4),
            item(5, mail_type="promotion"),
        ]
        body = format_morning_digest(messages, messages_found=5, now=NOW).body
        for expected in (
            "👀 要確認 1件", "🎓 大学・研究 1件", "💼 就活 1件",
            "💳 購入・請求 1件", "🔐 セキュリティ 1件", "📢 広告 1件",
        ):
            self.assertIn(expected, body)

    def test_action_details_are_capped(self) -> None:
        digest = format_morning_digest(
            [item(number, action=True) for number in range(8)],
            messages_found=8,
            now=NOW,
            action_detail_limit=3,
        )
        self.assertIn("ほか 5件", digest.body)
        self.assertEqual(
            sum(line.startswith("・") for line in digest.body.splitlines()), 3
        )

    def test_output_has_mobile_length_cap(self) -> None:
        digest = format_morning_digest(
            [item(number, action=True, subject="X" * 200) for number in range(10)],
            messages_found=10,
            now=NOW,
            action_detail_limit=10,
            max_length=180,
        )
        self.assertLessEqual(len(digest.body), 180)

    def test_sensitive_values_and_summary_are_not_exposed(self) -> None:
        digest = format_morning_digest(
            [
                item(1, action=True, subject="認証コード: 350295"),
                item(
                    2,
                    action=True,
                    organization="Security Service",
                    subject="982144 is your verification code",
                ),
            ],
            messages_found=2,
            now=NOW,
        )
        self.assertNotIn("350295", digest.body)
        self.assertNotIn("982144", digest.body)
        self.assertNotIn("This summary", digest.body)
        self.assertIn("[redacted]", digest.body)
        self.assertEqual(digest.title, "朝のメールまとめ | 10/08")


class MorningDigestNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = ClassificationStore(Path(self.temporary_directory.name) / "mail.db")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def process(
        self,
        client: Mock,
        *,
        requested: bool = True,
        dry_run: bool = False,
        now: datetime = NOW,
    ) -> MorningDigestNotificationStats:
        stats = MorningDigestNotificationStats()
        process_morning_digest_notification(
            [item(1, action=True)],
            self.store,
            messages_found=1,
            requested=requested,
            dry_run=dry_run,
            stats=stats,
            now=now,
            client_factory=lambda: client,
            error_stream=io.StringIO(),
        )
        return stats

    def test_not_requested_makes_no_call(self) -> None:
        client = Mock()
        stats = self.process(client, requested=False)
        client.assert_not_called()
        self.assertEqual(stats, MorningDigestNotificationStats())

    def test_requested_sends_once_on_same_tokyo_date(self) -> None:
        client = Mock()
        first = self.process(client)
        second = self.process(client, now=NOW + timedelta(hours=8))
        self.assertEqual(client.send_notification.call_count, 1)
        self.assertEqual(first.sent, 1)
        self.assertEqual(second.already_sent, 1)

    def test_next_tokyo_date_may_send(self) -> None:
        client = Mock()
        self.process(client)
        second = self.process(client, now=NOW + timedelta(days=1))
        self.assertEqual(client.send_notification.call_count, 2)
        self.assertEqual(second.sent, 1)

    def test_failed_send_is_not_recorded_and_can_retry(self) -> None:
        failing = Mock()
        failing.send_notification.side_effect = RuntimeError("secret details")
        first = self.process(failing)
        succeeding = Mock()
        second = self.process(succeeding)
        self.assertEqual(first.failures, 1)
        self.assertEqual(second.sent, 1)
        succeeding.send_notification.assert_called_once()

    def test_dry_run_has_preview_but_no_call_or_write(self) -> None:
        client = Mock()
        stats = self.process(client, dry_run=True)
        client.assert_not_called()
        self.assertEqual(stats.would_send, 1)
        self.assertIsNotNone(stats.preview)
        self.assertFalse(self.store.has_digest_notification("2026-10-08"))


if __name__ == "__main__":
    unittest.main()
