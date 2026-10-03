from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.mail.apply_labels import LabelStats, process_account_messages
from app.mail.classifier import EmailClassification
from app.mail.gmail.list_messages import MessageMetadata
from app.storage.classification_store import ClassificationStore


class FakeRequest:
    def __init__(self, operation: Callable[[], dict[str, Any]]) -> None:
        self.operation = operation

    def execute(self) -> dict[str, Any]:
        return self.operation()


class FakeLabels:
    def __init__(self, service: FakeService) -> None:
        self.service = service

    def list(self, **kwargs: Any) -> FakeRequest:
        return FakeRequest(lambda: {"labels": list(self.service.labels)})

    def create(self, **kwargs: Any) -> FakeRequest:
        def operation() -> dict[str, Any]:
            body = kwargs["body"]
            new_label = {
                "id": f"Label_{len(self.service.labels)}",
                "name": body["name"],
                "type": "user",
            }
            self.service.create_calls.append(kwargs)
            self.service.labels.append(new_label)
            return new_label

        return FakeRequest(operation)


class FakeMessages:
    def __init__(self, service: FakeService) -> None:
        self.service = service

    def get(self, **kwargs: Any) -> FakeRequest:
        return FakeRequest(
            lambda: {
                "id": kwargs["id"],
                "labelIds": sorted(self.service.message_labels[kwargs["id"]]),
            }
        )

    def modify(self, **kwargs: Any) -> FakeRequest:
        def operation() -> dict[str, Any]:
            self.service.modify_calls.append(kwargs)
            self.service.message_labels[kwargs["id"]].update(
                kwargs["body"]["addLabelIds"]
            )
            return {"id": kwargs["id"]}

        return FakeRequest(operation)


class FakeUsers:
    def __init__(self, service: FakeService) -> None:
        self._labels = FakeLabels(service)
        self._messages = FakeMessages(service)

    def labels(self) -> FakeLabels:
        return self._labels

    def messages(self) -> FakeMessages:
        return self._messages


class FakeService:
    def __init__(self) -> None:
        self.labels: list[dict[str, str]] = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_important", "name": "AI/重要", "type": "user"},
        ]
        self.message_labels = {"message-1": {"INBOX", "Label_important"}}
        self.create_calls: list[dict[str, Any]] = []
        self.modify_calls: list[dict[str, Any]] = []
        self._users = FakeUsers(self)

    def users(self) -> FakeUsers:
        return self._users


def metadata() -> MessageMetadata:
    return {
        "account_id": "google_2",
        "provider": "gmail",
        "account_email": "user@example.com",
        "message_id": "message-1",
        "thread_id": "thread-1",
        "from": "Sony <sender@example.com>",
        "subject": "採用イベント",
        "received_at": datetime(2026, 10, 4, tzinfo=timezone.utc),
    }


def saved_classification() -> EmailClassification:
    return EmailClassification.model_validate(
        {
            "domain": "job",
            "sender_type": "direct_organization",
            "mail_type": "event",
            "organization": "ソニーグループ",
            "importance": 4,
            "action_required": False,
            "reply_required": False,
            "deadline_at": None,
            "event_at": "2026-10-05T10:00:00+09:00",
            "summary": "採用イベントの案内です。",
            "confidence": 0.9,
        }
    )


class ApplyLabelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_directory.name) / "mail_agent.db"
        self.store = ClassificationStore(database_path)
        item = metadata()
        self.store.save_classification(
            provider=item["provider"],
            account_id=item["account_id"],
            message_id=item["message_id"],
            thread_id=item["thread_id"],
            from_address=item["from"],
            subject=item["subject"],
            received_at=item["received_at"].isoformat(),
            content_hash="hash",
            classification_json=saved_classification().model_dump_json(),
            model="deepseek-flash",
            classifier_version="v1",
        )
        self.service = FakeService()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_directory.cleanup()

    def process(self, *, apply: bool) -> LabelStats:
        stats = LabelStats()
        process_account_messages(
            self.service,
            [metadata()],
            self.store,
            apply=apply,
            stats=stats,
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
        return stats

    def test_dry_run_calls_no_gmail_mutation_api(self) -> None:
        stats = self.process(apply=False)
        self.assertEqual(stats.would_modify, 1)
        self.assertEqual(self.service.create_calls, [])
        self.assertEqual(self.service.modify_calls, [])

    def test_apply_creates_missing_label_and_adds_only_required_label(self) -> None:
        stats = self.process(apply=True)

        self.assertEqual(stats.modified, 1)
        self.assertEqual(
            [call["body"]["name"] for call in self.service.create_calls],
            ["AI/就活"],
        )
        self.assertEqual(len(self.service.modify_calls), 1)
        body = self.service.modify_calls[0]["body"]
        self.assertEqual(set(body), {"addLabelIds"})

    def test_apply_preserves_non_ai_and_existing_ai_labels(self) -> None:
        self.process(apply=True)
        current = self.service.message_labels["message-1"]
        self.assertIn("INBOX", current)
        self.assertIn("Label_important", current)

    def test_second_apply_is_idempotent(self) -> None:
        self.process(apply=True)
        create_count = len(self.service.create_calls)
        modify_count = len(self.service.modify_calls)

        stats = self.process(apply=True)

        self.assertEqual(stats.already_correct, 1)
        self.assertEqual(len(self.service.create_calls), create_count)
        self.assertEqual(len(self.service.modify_calls), modify_count)


if __name__ == "__main__":
    unittest.main()
