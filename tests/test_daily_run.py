from __future__ import annotations

import base64
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, patch

from app.ai.deepseek_client import DEEPSEEK_MODEL
from app.mail.apply_labels import LabelApplicationResult
from app.mail.classifier import CLASSIFIER_VERSION, EmailClassification
from app.mail.classify_recent import ClassifiedMessage, RecentMailClassifier
from app.mail.daily_run import DailyRunStats, process_account
from app.mail.digest import generate_daily_digest
from app.mail.gmail.list_messages import MessageMetadata
from app.storage.classification_store import ClassificationStore


def message(number: int, account: str = "google_1") -> MessageMetadata:
    return {
        "account_id": account,
        "provider": "gmail",
        "account_email": f"{account}@example.com",
        "message_id": f"{account}-message-{number}",
        "thread_id": f"thread-{number}",
        "from": "sender@example.com",
        "subject": f"Subject {number}",
        "received_at": datetime(2026, 10, 4, number, tzinfo=timezone.utc),
    }


def classification(**overrides: object) -> EmailClassification:
    values: dict[str, object] = {
        "domain": "service",
        "sender_type": "direct_organization",
        "mail_type": "information",
        "organization": "Example",
        "importance": 2,
        "action_required": False,
        "reply_required": False,
        "deadline_at": None,
        "event_at": None,
        "summary": "分類済みメールです。",
        "confidence": 0.9,
    }
    values.update(overrides)
    return EmailClassification.model_validate(values)


def full_message(text: str = "Body") -> dict[str, object]:
    encoded = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
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


class DailyRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = ClassificationStore(
            Path(self.temporary_directory.name) / "mail.db"
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def save_current(self, item: MessageMetadata) -> None:
        self.store.save_classification(
            provider=item["provider"],
            account_id=item["account_id"],
            message_id=item["message_id"],
            thread_id=item["thread_id"],
            from_address=item["from"],
            subject=item["subject"],
            received_at=item["received_at"].isoformat(),
            content_hash="immutable-message-hash",
            classification_json=classification().model_dump_json(),
            model=DEEPSEEK_MODEL,
            classifier_version=CLASSIFIER_VERSION,
        )

    def test_cached_message_skips_body_and_only_uncached_message_is_classified(
        self,
    ) -> None:
        cached = message(1)
        uncached = message(2)
        self.save_current(cached)
        processor = RecentMailClassifier(
            self.store,
            client_factory=lambda: object(),
        )
        stats = DailyRunStats(messages_found=2)

        with (
            patch(
                "app.mail.classify_recent.get_full_message",
                return_value=full_message(),
            ) as body_mock,
            patch(
                "app.mail.classify_recent.classify_email",
                return_value=classification(),
            ) as classify_mock,
            patch("app.mail.apply_labels.list_user_label_ids", return_value={}),
            patch("app.mail.apply_labels.get_message_label_ids", return_value=set()),
        ):
            results = process_account(
                object(), [cached, uncached], processor, stats, dry_run=True
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(stats.cache_hits, 1)
        self.assertEqual(stats.new_classifications, 1)
        body_mock.assert_called_once_with(ANY, uncached["message_id"])
        classify_mock.assert_called_once()

    def test_classification_is_followed_by_deterministic_label_policy(self) -> None:
        item = message(1)
        processor = RecentMailClassifier(
            self.store,
            client_factory=lambda: object(),
        )
        stats = DailyRunStats(messages_found=1)
        created_names: list[str] = []
        added_ids: list[set[str]] = []

        def create_label(service: object, name: str) -> str:
            created_names.append(name)
            return f"id:{name}"

        def add_labels(service: object, message_id: str, ids: set[str]) -> None:
            added_ids.append(ids)

        with (
            patch("app.mail.classify_recent.get_full_message", return_value=full_message()),
            patch(
                "app.mail.classify_recent.classify_email",
                return_value=classification(domain="job", importance=4),
            ),
            patch("app.mail.apply_labels.list_user_label_ids", return_value={}),
            patch("app.mail.apply_labels.get_message_label_ids", return_value=set()),
            patch(
                "app.mail.apply_labels.create_user_label", side_effect=create_label
            ),
            patch(
                "app.mail.apply_labels.add_labels_to_message", side_effect=add_labels
            ),
        ):
            process_account(object(), [item], processor, stats, dry_run=False)

        self.assertEqual(set(created_names), {"AI/重要", "AI/就活"})
        self.assertEqual(added_ids, [{"id:AI/重要", "id:AI/就活"}])
        self.assertEqual(stats.labels_added, 2)

    def test_dry_run_never_calls_gmail_mutation(self) -> None:
        item = message(1)
        self.save_current(item)
        processor = RecentMailClassifier(self.store)
        stats = DailyRunStats(messages_found=1)

        with (
            patch("app.mail.apply_labels.list_user_label_ids", return_value={}),
            patch("app.mail.apply_labels.get_message_label_ids", return_value=set()),
            patch("app.mail.apply_labels.create_user_label") as create_mock,
            patch("app.mail.apply_labels.add_labels_to_message") as modify_mock,
        ):
            process_account(object(), [item], processor, stats, dry_run=True)

        create_mock.assert_not_called()
        modify_mock.assert_not_called()
        self.assertEqual(stats.would_add_labels, 1)

    def test_classification_failure_does_not_stop_next_message(self) -> None:
        items = [message(1), message(2)]
        processor = RecentMailClassifier(
            self.store,
            client_factory=lambda: object(),
        )
        stats = DailyRunStats(messages_found=2)
        with (
            patch("app.mail.classify_recent.get_full_message", return_value=full_message()),
            patch(
                "app.mail.classify_recent.classify_email",
                side_effect=[RuntimeError("private"), classification()],
            ),
            patch("app.mail.apply_labels.list_user_label_ids", return_value={}),
            patch("app.mail.apply_labels.get_message_label_ids", return_value=set()),
        ):
            results = process_account(
                object(),
                items,
                processor,
                stats,
                dry_run=True,
                error_stream=io.StringIO(),
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(stats.classification_failures, 1)

    def test_label_failure_does_not_stop_next_message(self) -> None:
        items = [message(1), message(2)]
        for item in items:
            self.save_current(item)
        processor = RecentMailClassifier(self.store)
        stats = DailyRunStats(messages_found=2)
        successful_result = LabelApplicationResult(frozenset(), frozenset())

        with patch(
            "app.mail.daily_run.GmailLabelApplier"
        ) as applier_class:
            applier_class.return_value.process.side_effect = [
                RuntimeError("private"),
                successful_result,
            ]
            results = process_account(
                object(),
                items,
                processor,
                stats,
                dry_run=False,
                error_stream=io.StringIO(),
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(stats.label_failures, 1)
        self.assertEqual(stats.already_labeled, 1)

    def test_same_pipeline_processor_handles_multiple_accounts(self) -> None:
        first = message(1, "google_1")
        second = message(2, "google_2")
        self.save_current(first)
        self.save_current(second)
        processor = RecentMailClassifier(self.store)
        stats = DailyRunStats(accounts=2, messages_found=2)

        with (
            patch("app.mail.apply_labels.list_user_label_ids", return_value={}),
            patch("app.mail.apply_labels.get_message_label_ids", return_value=set()),
        ):
            one = process_account(object(), [first], processor, stats, dry_run=True)
            two = process_account(object(), [second], processor, stats, dry_run=True)

        self.assertEqual(len(one + two), 2)
        self.assertEqual(stats.cache_hits, 2)


class DigestTests(unittest.TestCase):
    def render(
        self,
        classifications: list[EmailClassification],
        *,
        messages_found: int | None = None,
    ) -> str:
        items = [
            ClassifiedMessage(message(index + 1), value, "cache")
            for index, value in enumerate(classifications)
        ]
        end = datetime(2026, 10, 4, tzinfo=timezone.utc)
        return generate_daily_digest(
            items,
            period_start=end - timedelta(hours=24),
            period_end=end,
            accounts=4,
            messages_found=(len(items) if messages_found is None else messages_found),
            new_classifications=0,
            cache_hits=len(items),
        )

    def test_header_distinguishes_found_classified_and_unclassified(self) -> None:
        digest = self.render([classification() for _ in range(19)], messages_found=25)
        self.assertIn("Messages found: 25", digest)
        self.assertIn("Classified: 19", digest)
        self.assertIn("Skipped/unclassified: 6", digest)
        self.assertIn("未分類メール: 6件", digest)

    def test_important_message_is_in_review_section(self) -> None:
        digest = self.render([classification(importance=4)])
        self.assertIn("[要確認]", digest)

    def test_action_or_reply_is_in_action_section(self) -> None:
        digest = self.render(
            [classification(action_required=True), classification(reply_required=True)]
        )
        self.assertIn("[要対応]", digest)
        self.assertEqual(digest.count("分類済みメールです。"), 2)

    def test_required_deadline_is_in_required_deadline_section(self) -> None:
        digest = self.render(
            [
                classification(
                    deadline_at="2026-10-05T12:00:00+09:00",
                    action_required=True,
                )
            ]
        )
        self.assertIn("[要対応期限]", digest)
        self.assertIn("10/05 12:00", digest)

    def test_optional_deadline_is_in_reference_deadline_section(self) -> None:
        digest = self.render(
            [classification(deadline_at="2026-10-03T23:55:00+09:00")]
        )
        self.assertIn("[参考期限]", digest)
        self.assertIn("10/03 23:55", digest)

    def test_many_promotions_are_counted_without_listing_each(self) -> None:
        promotions = [
            classification(
                mail_type="promotion",
                importance=1,
                summary=f"広告 {number}",
            )
            for number in range(20)
        ]
        digest = self.render(promotions)
        self.assertIn("[広告]\n20件", digest)
        self.assertNotIn("広告 0", digest)

    def test_low_importance_platform_job_mail_is_limited_to_three(self) -> None:
        general_job_mail = [
            classification(
                domain="job",
                sender_type="platform",
                importance=2,
                organization=f"Platform {number}",
                summary=f"一般案内 {number}",
            )
            for number in range(10)
        ]
        digest = self.render(general_job_mail)
        self.assertEqual(digest.count("一般案内 "), 3)
        self.assertIn("その他の就活案内: 7件", digest)

    def test_priority_job_mail_is_displayed_before_platform_general_mail(self) -> None:
        general = classification(
            domain="job",
            sender_type="platform",
            importance=2,
            summary="一般案内",
        )
        direct = classification(
            domain="job",
            sender_type="direct_organization",
            importance=2,
            summary="企業から直接",
        )
        important = classification(
            domain="job",
            sender_type="platform",
            importance=3,
            summary="重要度3",
        )
        digest = self.render([general, direct, important])
        self.assertLess(digest.index("企業から直接"), digest.index("一般案内"))
        self.assertLess(digest.index("重要度3"), digest.index("一般案内"))

    def test_empty_sections_are_omitted_and_zero_unclassified_is_explicit(self) -> None:
        digest = self.render([classification(domain="service")])
        self.assertNotIn("[要対応]", digest)
        self.assertNotIn("[広告]", digest)
        self.assertIn("Skipped/unclassified: 0", digest)
        self.assertNotIn("未分類メール:", digest)


if __name__ == "__main__":
    unittest.main()
