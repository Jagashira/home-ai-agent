from __future__ import annotations

import base64
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from app.ai.deepseek_client import DEEPSEEK_MODEL
from app.mail.apply_labels import LabelApplicationResult
from app.mail.classifier import CLASSIFIER_VERSION, EmailClassification
from app.mail.classify_recent import ClassifiedMessage, RecentMailClassifier
from app.mail.daily_run import (
    DailyRunStats,
    main,
    parse_args,
    print_pipeline_summary,
    process_account,
)
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
    def render_items(
        self,
        items: list[ClassifiedMessage],
        *,
        messages_found: int | None = None,
    ) -> str:
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
        return self.render_items(
            items,
            messages_found=messages_found,
        )

    def test_cross_account_duplicate_is_shown_once_with_accounts(self) -> None:
        value = classification(
            organization="Google",
            summary="同一のセキュリティ通知です。",
        )
        items = [
            ClassifiedMessage(message(1, "google_2"), value, "cache"),
            ClassifiedMessage(message(1, "google_3"), value, "cache"),
        ]
        digest = self.render_items(items)
        self.assertEqual(digest.count("同一のセキュリティ通知です。"), 1)
        self.assertIn("Accounts: google_2, google_3", digest)
        self.assertIn("Duplicate digest messages collapsed: 1", digest)

    def test_duplicate_normalization_keeps_newest_message(self) -> None:
        older_metadata = message(1, "google_2")
        older_metadata["subject"] = "【採用】  結果のお知らせ"
        newer_metadata = message(2, "google_3")
        newer_metadata["subject"] = "採用 結果のお知らせ"
        items = [
            ClassifiedMessage(
                older_metadata,
                classification(organization="ＡＣＭＥ", summary="古い通知"),
                "cache",
            ),
            ClassifiedMessage(
                newer_metadata,
                classification(organization="ACME", summary="新しい通知"),
                "cache",
            ),
        ]
        digest = self.render_items(items)
        self.assertNotIn("古い通知", digest)
        self.assertIn("新しい通知", digest)

    def test_same_subject_different_organizations_are_not_aggregated(self) -> None:
        items = [
            ClassifiedMessage(
                message(1, "google_2"),
                classification(organization="Company A", summary="通知A"),
                "cache",
            ),
            ClassifiedMessage(
                message(1, "google_3"),
                classification(organization="Company B", summary="通知B"),
                "cache",
            ),
        ]
        digest = self.render_items(items)
        self.assertIn("通知A", digest)
        self.assertIn("通知B", digest)

    def test_different_subjects_are_not_aggregated(self) -> None:
        second_metadata = message(1, "google_3")
        second_metadata["subject"] = "Different subject"
        items = [
            ClassifiedMessage(
                message(1, "google_2"),
                classification(organization="Google", summary="通知Aです。"),
                "cache",
            ),
            ClassifiedMessage(
                second_metadata,
                classification(organization="Google", summary="通知Bです。"),
                "cache",
            ),
        ]
        digest = self.render_items(items)
        self.assertIn("通知Aです。", digest)
        self.assertIn("通知Bです。", digest)
        self.assertNotIn("Accounts: google_2, google_3", digest)

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

    def test_future_optional_deadline_is_in_reference_deadline_section(self) -> None:
        digest = self.render(
            [classification(deadline_at="2026-10-05T23:55:00+09:00")]
        )
        self.assertIn("[参考期限]", digest)
        self.assertIn("10/05 23:55", digest)

    def test_expired_actionable_and_reference_deadlines_are_suppressed(self) -> None:
        digest = self.render(
            [
                classification(
                    deadline_at="2026-10-04T08:59:00+09:00",
                    action_required=True,
                    summary="期限切れ要対応",
                ),
                classification(
                    deadline_at="2026-10-04T08:58:00+09:00",
                    summary="期限切れ参考",
                ),
            ]
        )
        self.assertNotIn("期限切れ要対応", digest)
        self.assertNotIn("期限切れ参考", digest)
        self.assertIn("Expired digest deadlines suppressed: 2", digest)

    def test_deadline_equal_to_now_remains(self) -> None:
        digest = self.render(
            [
                classification(
                    deadline_at="2026-10-04T09:00:00+09:00",
                    action_required=True,
                )
            ]
        )
        self.assertIn("[要対応期限]", digest)
        self.assertIn("Expired digest deadlines suppressed: 0", digest)

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

    def test_low_importance_platform_job_mail_obeys_cap(self) -> None:
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
        self.assertEqual(digest.count("一般案内 "), 5)
        self.assertIn("その他の就活案内: 5件", digest)

    def test_direct_low_importance_information_is_an_ordinary_candidate(self) -> None:
        digest = self.render(
            [
                classification(
                    domain="job",
                    sender_type="direct_organization",
                    mail_type="information",
                    importance=2,
                    summary="単なる完了通知",
                )
            ]
        )
        self.assertNotIn("その他の就活案内:", digest)
        self.assertIn("単なる完了通知", digest)

    def test_priority_job_mail_types_are_displayed(self) -> None:
        values = [
            classification(
                domain="job",
                sender_type="platform",
                mail_type=mail_type,
                importance=2,
                summary=f"{mail_type}詳細",
            )
            for mail_type in ("selection", "result", "event", "action_required")
        ]
        digest = self.render(values)
        for mail_type in ("selection", "result", "event", "action_required"):
            self.assertIn(f"{mail_type}詳細", digest)

    def test_twenty_ordinary_job_messages_show_five_and_suppress_fifteen(self) -> None:
        values = [
            classification(
                domain="job",
                mail_type="event",
                importance=2,
                organization=f"Company {number}",
                summary=f"任意イベント {number}",
            )
            for number in range(20)
        ]
        digest = self.render(values)
        self.assertEqual(digest.count("任意イベント "), 5)
        self.assertIn("その他の就活案内: 15件", digest)

    def test_actionable_job_messages_are_not_hidden_by_job_cap(self) -> None:
        values = [
            classification(
                domain="job",
                action_required=True,
                organization=f"Company {number}",
                summary=f"必須対応 {number}",
            )
            for number in range(8)
        ]
        digest = self.render(values)
        self.assertEqual(digest.count("必須対応 "), 8)

    def test_selection_and_result_messages_bypass_ordinary_cap(self) -> None:
        ordinary = [
            classification(
                domain="job",
                organization=f"Ordinary {number}",
                summary=f"ordinary {number}",
            )
            for number in range(20)
        ]
        exempt = [
            classification(
                domain="job",
                mail_type="selection",
                organization="Selection Company",
                summary="selection bypass",
            ),
            classification(
                domain="job",
                mail_type="result",
                organization="Result Company",
                summary="result bypass",
            ),
        ]
        digest = self.render([*ordinary, *exempt])
        self.assertIn("selection bypass", digest)
        self.assertIn("result bypass", digest)
        self.assertEqual(digest.count("ordinary "), 5)
        self.assertIn("その他の就活案内: 15件", digest)

    def test_importance_orders_ordinary_candidates_without_exempting_them(self) -> None:
        values = [
            classification(
                domain="job",
                importance=3,
                organization="High importance",
                summary="importance winner",
            ),
            *[
                classification(
                    domain="job",
                    importance=2,
                    organization=f"Low {number}",
                    summary=("importance loser" if number == 0 else f"low {number}"),
                )
                for number in range(5)
            ],
        ]
        digest = self.render(values)
        self.assertIn("importance winner", digest)
        self.assertNotIn("importance loser", digest)
        self.assertIn("その他の就活案内: 1件", digest)

    def test_direct_organization_wins_sender_type_tie(self) -> None:
        values = [
            classification(
                domain="job",
                sender_type="direct_organization",
                organization="Direct",
                summary="direct winner",
            ),
            *[
                classification(
                    domain="job",
                    sender_type="platform",
                    organization=f"Platform {number}",
                    summary=("platform loser" if number == 0 else f"platform {number}"),
                )
                for number in range(5)
            ],
        ]
        digest = self.render(values)
        self.assertIn("direct winner", digest)
        self.assertNotIn("platform loser", digest)

    def test_newer_received_at_wins_remaining_tie(self) -> None:
        values = [
            classification(
                domain="job",
                sender_type="platform",
                organization=f"Platform {number}",
                summary=("oldest loser" if number == 0 else f"newer {number}"),
            )
            for number in range(6)
        ]
        digest = self.render(values)
        self.assertNotIn("oldest loser", digest)
        for number in range(1, 6):
            self.assertIn(f"newer {number}", digest)

    def test_empty_sections_are_omitted_and_zero_unclassified_is_explicit(self) -> None:
        digest = self.render([classification(domain="service")])
        self.assertNotIn("[要対応]", digest)
        self.assertNotIn("[広告]", digest)
        self.assertIn("Skipped/unclassified: 0", digest)
        self.assertNotIn("未分類メール:", digest)


class DailyRunSummaryTests(unittest.TestCase):
    def test_dry_run_metric_is_named_as_label_assignments(self) -> None:
        stats = DailyRunStats(labels_added=2, would_add_labels=5)
        output = io.StringIO()
        with redirect_stdout(output):
            print_pipeline_summary(stats, dry_run=True)
        rendered = output.getvalue()
        self.assertIn("Label assignments added: 2", rendered)
        self.assertIn("Would add label assignments: 5", rendered)
        self.assertNotIn("Labels added:", rendered)
        self.assertNotIn("Would add labels:", rendered)

    def test_dry_run_summary_includes_home_assistant_action_counts(self) -> None:
        stats = DailyRunStats()
        stats.home_assistant.would_add_tasks = 2
        stats.home_assistant.would_send_notifications = 2
        stats.home_assistant.expired_skipped = 1
        stats.home_assistant.superseded_skipped = 3
        output = io.StringIO()
        with redirect_stdout(output):
            print_pipeline_summary(stats, dry_run=True)
        rendered = output.getvalue()
        self.assertIn("Home Assistant tasks added: 0", rendered)
        self.assertIn("Would add Home Assistant tasks: 2", rendered)
        self.assertIn("Would send notifications: 2", rendered)
        self.assertIn("Expired Home Assistant actions skipped: 1", rendered)
        self.assertIn("Superseded Home Assistant actions skipped: 3", rendered)
        self.assertIn("Home Assistant failures: 0", rendered)


class DailyRunCliTests(unittest.TestCase):
    def test_notify_digest_is_opt_in(self) -> None:
        with patch("sys.argv", ["daily_run"]):
            self.assertFalse(parse_args().notify_digest)
        with patch("sys.argv", ["daily_run", "--notify-digest"]):
            self.assertTrue(parse_args().notify_digest)

    def test_main_passes_default_and_dry_run_notification_flags(self) -> None:
        for argv, expected_requested, expected_dry_run in (
            (["daily_run", "--account", "google_1"], False, False),
            (
                [
                    "daily_run", "--account", "google_1", "--dry-run",
                    "--notify-digest",
                ],
                True,
                True,
            ),
        ):
            with self.subTest(argv=argv):
                store_class = MagicMock()
                store_class.return_value.__enter__.return_value = MagicMock()
                with (
                    patch("sys.argv", argv),
                    patch("app.mail.daily_run.ClassificationStore", store_class),
                    patch("app.mail.daily_run.get_gmail_service", return_value=object()),
                    patch("app.mail.daily_run.get_account_email", return_value="a@example.com"),
                    patch("app.mail.daily_run.fetch_recent_messages", return_value=[]),
                    patch("app.mail.daily_run.process_home_assistant_actions"),
                    patch("app.mail.daily_run.process_morning_digest_notification") as morning,
                    redirect_stdout(io.StringIO()),
                ):
                    result = main()
                self.assertEqual(result, 0)
                self.assertEqual(morning.call_args.kwargs["requested"], expected_requested)
                self.assertEqual(morning.call_args.kwargs["dry_run"], expected_dry_run)

    def test_morning_digest_failure_does_not_prevent_terminal_digest(self) -> None:
        store_class = MagicMock()
        store_class.return_value.__enter__.return_value = MagicMock()
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch("sys.argv", ["daily_run", "--account", "google_1", "--notify-digest"]),
            patch("app.mail.daily_run.ClassificationStore", store_class),
            patch("app.mail.daily_run.get_gmail_service", return_value=object()),
            patch("app.mail.daily_run.get_account_email", return_value="a@example.com"),
            patch("app.mail.daily_run.fetch_recent_messages", return_value=[]),
            patch("app.mail.daily_run.process_home_assistant_actions"),
            patch(
                "app.mail.daily_run.process_morning_digest_notification",
                side_effect=RuntimeError("token must stay hidden"),
            ),
            redirect_stdout(output),
            patch("sys.stderr", errors),
        ):
            result = main()
        self.assertEqual(result, 0)
        self.assertIn("=== Daily Mail Digest ===", output.getvalue())
        self.assertIn("Morning digest notification failed", errors.getvalue())
        self.assertNotIn("token must stay hidden", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
