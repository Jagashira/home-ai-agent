from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from app.integrations.home_assistant.client import HomeAssistantConfigurationError
from app.mail.classifier import EmailClassification
from app.mail.classify_recent import ClassifiedMessage
from app.mail.digest import generate_daily_digest
from app.mail.gmail.list_messages import MessageMetadata
from app.mail.home_assistant_actions import (
    HomeAssistantActionStats,
    process_home_assistant_actions,
)
from app.storage.classification_store import ClassificationStore


TEST_NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)


def classified_message(
    number: int,
    *,
    action_required: bool = False,
    reply_required: bool = False,
    deadline_at: str | None = None,
    organization: str = "Example Company",
    subject: str | None = None,
    received_at: datetime | None = None,
) -> ClassifiedMessage:
    metadata: MessageMetadata = {
        "account_id": "google_2",
        "provider": "gmail",
        "account_email": "user@example.com",
        "message_id": f"message-{number}",
        "thread_id": f"thread-{number}",
        "from": "sender@example.com",
        "subject": subject or f"Subject {number}",
        "received_at": received_at
        or datetime(2026, 10, 4, number, tzinfo=timezone.utc),
    }
    classification = EmailClassification.model_validate(
        {
            "domain": "job",
            "sender_type": "direct_organization",
            "mail_type": "action_required" if action_required else "information",
            "organization": organization,
            "importance": 4 if action_required or reply_required else 2,
            "action_required": action_required,
            "reply_required": reply_required,
            "deadline_at": deadline_at,
            "event_at": None,
            "summary": f"対応が必要なメール {number} です。",
            "confidence": 0.9,
        }
    )
    return ClassifiedMessage(metadata, classification, "cache")


class HomeAssistantActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "mail.db"
        self.store = ClassificationStore(self.database_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def process(
        self,
        messages: list[ClassifiedMessage],
        client: Mock,
        *,
        dry_run: bool = False,
        now: datetime = TEST_NOW,
    ) -> HomeAssistantActionStats:
        stats = HomeAssistantActionStats()
        process_home_assistant_actions(
            messages,
            self.store,
            dry_run=dry_run,
            stats=stats,
            client_factory=lambda: client,
            error_stream=io.StringIO(),
            now=now,
        )
        return stats

    def state(self, item: ClassifiedMessage):
        metadata = item.metadata
        return self.store.get_mail_action_state(
            provider=metadata["provider"],
            account_id=metadata["account_id"],
            message_id=metadata["message_id"],
        )

    def mark_task(self, item: ClassifiedMessage) -> None:
        self.store.mark_ha_task_created(
            provider=item.metadata["provider"],
            account_id=item.metadata["account_id"],
            message_id=item.metadata["message_id"],
        )

    def mark_notification(self, item: ClassifiedMessage) -> None:
        self.store.mark_ha_notified(
            provider=item.metadata["provider"],
            account_id=item.metadata["account_id"],
            message_id=item.metadata["message_id"],
        )

    def test_action_required_creates_task_and_notification(self) -> None:
        item = classified_message(1, action_required=True)
        client = Mock()

        stats = self.process([item], client)

        client.add_todo_item.assert_called_once()
        client.send_notification.assert_called_once_with(
            title="要対応メール",
            message="Example Company | Subject 1\n対応が必要なメール 1 です。",
        )
        self.assertEqual(stats.tasks_added, 1)
        self.assertEqual(stats.notifications_sent, 1)
        self.assertIsNotNone(self.state(item).ha_task_created_at)
        self.assertIsNotNone(self.state(item).ha_notified_at)

    def test_reply_required_creates_task_and_notification(self) -> None:
        item = classified_message(1, reply_required=True)
        client = Mock()

        stats = self.process([item], client)

        self.assertEqual(stats.tasks_added, 1)
        self.assertEqual(stats.notifications_sent, 1)

    def test_non_actionable_email_does_nothing(self) -> None:
        client = Mock()
        stats = self.process([classified_message(1)], client)
        client.assert_not_called()
        self.assertEqual(stats, HomeAssistantActionStats())

    def test_exact_deadline_is_sent_as_due_datetime(self) -> None:
        item = classified_message(
            1,
            action_required=True,
            deadline_at="2026-10-05T12:00:00+09:00",
        )
        client = Mock()

        self.process([item], client)

        call = client.add_todo_item.call_args.kwargs
        self.assertEqual(call["due_datetime"], "2026-10-05T12:00:00+09:00")
        self.assertIn("Deadline: 2026-10-05T12:00:00+09:00", call["description"])
        self.assertIn("期限: 10/05 12:00", client.send_notification.call_args.kwargs["message"])

    def test_no_deadline_passes_no_due_datetime(self) -> None:
        client = Mock()
        self.process([classified_message(1, action_required=True)], client)
        self.assertIsNone(client.add_todo_item.call_args.kwargs["due_datetime"])

    def test_dry_run_has_no_external_calls_or_database_writes(self) -> None:
        item = classified_message(1, action_required=True)
        client_factory = Mock(side_effect=AssertionError("must not be called"))
        stats = HomeAssistantActionStats()

        process_home_assistant_actions(
            [item],
            self.store,
            dry_run=True,
            stats=stats,
            client_factory=client_factory,
            error_stream=io.StringIO(),
            now=TEST_NOW,
        )

        client_factory.assert_not_called()
        self.assertEqual(stats.would_add_tasks, 1)
        self.assertEqual(stats.would_send_notifications, 1)
        self.assertIsNone(self.state(item).ha_task_created_at)
        self.assertIsNone(self.state(item).ha_notified_at)

    def test_authentication_code_is_redacted_from_external_text(self) -> None:
        item = classified_message(1, action_required=True)
        unsafe_classification = item.classification.model_copy(
            update={"summary": "認証コードは350295です。"}
        )
        item = ClassifiedMessage(item.metadata, unsafe_classification, "cache")
        client = Mock()

        self.process([item], client)

        todo_call = client.add_todo_item.call_args.kwargs
        notification_call = client.send_notification.call_args.kwargs
        self.assertNotIn("350295", todo_call["description"])
        self.assertNotIn("350295", notification_call["message"])
        self.assertIn("[redacted]", todo_call["description"])

    def test_confirmation_code_in_subject_is_redacted_from_ha_outputs(self) -> None:
        item = classified_message(
            1,
            action_required=True,
            subject="Your X confirmation code is xehwxzpk",
        )
        client = Mock()

        self.process([item], client)

        todo_call = client.add_todo_item.call_args.kwargs
        notification_call = client.send_notification.call_args.kwargs
        self.assertNotIn("xehwxzpk", todo_call["item"])
        self.assertNotIn("xehwxzpk", todo_call["description"])
        self.assertNotIn("xehwxzpk", notification_call["message"])
        self.assertIn("[redacted]", notification_call["message"])

    def test_existing_task_only_sends_notification(self) -> None:
        item = classified_message(1, action_required=True)
        self.mark_task(item)
        client = Mock()

        stats = self.process([item], client)

        client.add_todo_item.assert_not_called()
        client.send_notification.assert_called_once()
        self.assertEqual(stats.tasks_already_present, 1)

    def test_existing_notification_only_creates_task(self) -> None:
        item = classified_message(1, action_required=True)
        self.mark_notification(item)
        client = Mock()

        stats = self.process([item], client)

        client.add_todo_item.assert_called_once()
        client.send_notification.assert_not_called()
        self.assertEqual(stats.notifications_already_sent, 1)

    def test_both_existing_causes_no_external_calls(self) -> None:
        item = classified_message(1, action_required=True)
        self.mark_task(item)
        self.mark_notification(item)
        client = Mock()

        stats = self.process([item], client)

        client.assert_not_called()
        self.assertEqual(stats.tasks_already_present, 1)
        self.assertEqual(stats.notifications_already_sent, 1)

    def test_notification_failure_retries_only_notification(self) -> None:
        item = classified_message(1, action_required=True)
        first_client = Mock()
        first_client.send_notification.side_effect = RuntimeError("failure")
        first_stats = self.process([item], first_client)
        self.assertEqual(first_stats.tasks_added, 1)
        self.assertIsNone(self.state(item).ha_notified_at)

        second_client = Mock()
        second_stats = self.process([item], second_client)
        second_client.add_todo_item.assert_not_called()
        second_client.send_notification.assert_called_once()
        self.assertEqual(second_stats.tasks_already_present, 1)

    def test_task_failure_retries_only_task(self) -> None:
        item = classified_message(1, action_required=True)
        first_client = Mock()
        first_client.add_todo_item.side_effect = RuntimeError("failure")
        first_stats = self.process([item], first_client)
        self.assertEqual(first_stats.notifications_sent, 1)
        self.assertIsNone(self.state(item).ha_task_created_at)

        second_client = Mock()
        second_stats = self.process([item], second_client)
        second_client.add_todo_item.assert_called_once()
        second_client.send_notification.assert_not_called()
        self.assertEqual(second_stats.notifications_already_sent, 1)

    def test_one_failure_does_not_stop_later_email(self) -> None:
        first = classified_message(1, action_required=True)
        second = classified_message(2, action_required=True)
        client = Mock()
        client.add_todo_item.side_effect = [RuntimeError("failure"), None]

        stats = self.process([first, second], client)

        self.assertEqual(client.add_todo_item.call_count, 2)
        self.assertEqual(client.send_notification.call_count, 2)
        self.assertEqual(stats.failures, 1)
        self.assertIsNotNone(self.state(first).ha_task_created_at)

    def test_unavailable_configuration_is_safe_and_does_not_raise(self) -> None:
        item = classified_message(1, action_required=True)
        stats = HomeAssistantActionStats()
        errors = io.StringIO()

        process_home_assistant_actions(
            [item],
            self.store,
            dry_run=False,
            stats=stats,
            client_factory=lambda: (_ for _ in ()).throw(
                HomeAssistantConfigurationError("missing configuration")
            ),
            error_stream=errors,
            now=TEST_NOW,
        )

        self.assertEqual(stats.failures, 2)
        self.assertIn("configuration unavailable", errors.getvalue())
        self.assertNotIn("missing configuration", errors.getvalue())
        period_end = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
        digest = generate_daily_digest(
            [item],
            period_start=period_end - timedelta(hours=24),
            period_end=period_end,
            accounts=1,
            messages_found=1,
            new_classifications=0,
            cache_hits=1,
        )
        self.assertIn("Subject 1", digest)

    def test_expired_exact_deadline_is_skipped(self) -> None:
        item = classified_message(
            1,
            action_required=True,
            deadline_at="2026-10-05T08:59:00+09:00",
        )
        client = Mock()

        stats = self.process([item], client)

        client.add_todo_item.assert_not_called()
        client.send_notification.assert_not_called()
        self.assertEqual(stats.expired_skipped, 1)
        self.assertIsNone(self.state(item).ha_task_created_at)
        self.assertIsNone(self.state(item).ha_notified_at)

    def test_future_exact_deadline_is_processed(self) -> None:
        item = classified_message(
            1,
            action_required=True,
            deadline_at="2026-10-05T09:01:00+09:00",
        )
        stats = self.process([item], Mock())
        self.assertEqual(stats.tasks_added, 1)
        self.assertEqual(stats.notifications_sent, 1)

    def test_no_deadline_is_processed_by_expiry_policy(self) -> None:
        stats = self.process(
            [classified_message(1, action_required=True)],
            Mock(),
        )
        self.assertEqual(stats.tasks_added, 1)
        self.assertEqual(stats.expired_skipped, 0)

    def test_deadline_equal_to_now_is_processed(self) -> None:
        item = classified_message(
            1,
            action_required=True,
            deadline_at="2026-10-05T09:00:00+09:00",
        )
        stats = self.process([item], Mock())
        self.assertEqual(stats.tasks_added, 1)
        self.assertEqual(stats.expired_skipped, 0)

    def test_same_case_keeps_only_newest_received_email(self) -> None:
        organization = "個人番号カード交付申請書受付センター"
        older = classified_message(
            1,
            action_required=True,
            organization=organization,
            subject=" 【個人番号カード】  申請情報登録URLのご案内 ",
            received_at=datetime(2026, 10, 4, 1, tzinfo=timezone.utc),
        )
        newer = classified_message(
            2,
            action_required=True,
            organization=organization,
            subject="個人番号カード 申請情報登録URLのご案内",
            received_at=datetime(2026, 10, 4, 2, tzinfo=timezone.utc),
        )
        client = Mock()

        stats = self.process([older, newer], client)

        self.assertEqual(client.add_todo_item.call_count, 1)
        self.assertIn(
            newer.metadata["subject"],
            client.add_todo_item.call_args.kwargs["item"],
        )
        self.assertEqual(stats.superseded_skipped, 1)
        self.assertIsNone(self.state(older).ha_task_created_at)
        self.assertIsNotNone(self.state(newer).ha_task_created_at)

    def test_same_subject_with_different_organizations_is_not_deduplicated(self) -> None:
        messages = [
            classified_message(
                1,
                action_required=True,
                organization="Organization A",
                subject="Same subject",
            ),
            classified_message(
                2,
                action_required=True,
                organization="Organization B",
                subject="Same subject",
            ),
        ]
        client = Mock()
        stats = self.process(messages, client)
        self.assertEqual(client.add_todo_item.call_count, 2)
        self.assertEqual(stats.superseded_skipped, 0)

    def test_same_organization_with_different_subjects_is_not_deduplicated(self) -> None:
        messages = [
            classified_message(1, action_required=True, subject="Subject A"),
            classified_message(2, action_required=True, subject="Subject B"),
        ]
        client = Mock()
        stats = self.process(messages, client)
        self.assertEqual(client.add_todo_item.call_count, 2)
        self.assertEqual(stats.superseded_skipped, 0)

    def test_dry_run_reports_expired_and_superseded_counts(self) -> None:
        expired = classified_message(
            1,
            action_required=True,
            deadline_at="2026-10-05T08:59:00+09:00",
            subject="Expired",
        )
        older = classified_message(
            2,
            action_required=True,
            subject="【Duplicate】",
            received_at=datetime(2026, 10, 4, 2, tzinfo=timezone.utc),
        )
        newer = classified_message(
            3,
            action_required=True,
            subject="Duplicate",
            received_at=datetime(2026, 10, 4, 3, tzinfo=timezone.utc),
        )
        client_factory = Mock(side_effect=AssertionError("must not be called"))
        stats = HomeAssistantActionStats()

        process_home_assistant_actions(
            [expired, older, newer],
            self.store,
            dry_run=True,
            stats=stats,
            client_factory=client_factory,
            error_stream=io.StringIO(),
            now=TEST_NOW,
        )

        client_factory.assert_not_called()
        self.assertEqual(stats.expired_skipped, 1)
        self.assertEqual(stats.superseded_skipped, 1)
        self.assertEqual(stats.would_add_tasks, 1)
        self.assertEqual(stats.would_send_notifications, 1)

    def test_mail_actions_schema_contains_no_message_content(self) -> None:
        columns = {
            row[1]
            for row in sqlite3.connect(self.database_path)
            .execute("PRAGMA table_info(mail_actions)")
            .fetchall()
        }
        self.assertEqual(
            columns,
            {
                "provider",
                "account_id",
                "message_id",
                "ha_task_created_at",
                "ha_notified_at",
            },
        )


if __name__ == "__main__":
    unittest.main()
