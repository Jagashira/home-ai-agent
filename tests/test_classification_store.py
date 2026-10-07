"""Tests for the SQLite classification cache."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.storage.classification_store import ClassificationStore


class ClassificationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "mail.db"
        self.store = ClassificationStore(self.database_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _save(self) -> None:
        self.store.save_classification(
            provider="gmail",
            account_id="google_1",
            message_id="message-1",
            thread_id="thread-1",
            from_address="sender@example.com",
            subject="Subject",
            received_at="2026-10-04T00:00:00+00:00",
            content_hash="hash-v1",
            classification_json='{"domain":"service"}',
            model="deepseek-flash",
            classifier_version="v1",
            classified_at="2026-10-04T00:01:00+00:00",
        )

    def _get(self, **overrides: str) -> str | None:
        values = {
            "provider": "gmail",
            "account_id": "google_1",
            "message_id": "message-1",
            "content_hash": "hash-v1",
            "model": "deepseek-flash",
            "classifier_version": "v1",
        }
        values.update(overrides)
        return self.store.get_cached_classification(**values)

    def test_exact_cache_identity_is_a_hit(self) -> None:
        self._save()
        self.assertEqual(self._get(), '{"domain":"service"}')

    def test_content_model_and_classifier_version_changes_are_misses(self) -> None:
        self._save()
        for field, value in (
            ("content_hash", "hash-v2"),
            ("model", "different-model"),
            ("classifier_version", "v2"),
        ):
            with self.subTest(field=field):
                self.assertIsNone(self._get(**{field: value}))

    def test_database_schema_does_not_store_message_body_or_secrets(self) -> None:
        private_body = "PRIVATE_NORMALIZED_BODY_SHOULD_NOT_BE_STORED"
        self._save()
        self.store.close()

        connection = sqlite3.connect(self.database_path)
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(mail_classifications)"
            ).fetchall()
        }
        connection.close()

        self.assertNotIn("body", columns)
        self.assertNotIn("normalized_body", columns)
        self.assertNotIn("raw_body", columns)
        self.assertNotIn("oauth_token", columns)
        self.assertNotIn("api_key", columns)
        self.assertNotIn(private_body.encode(), self.database_path.read_bytes())

        self.store = ClassificationStore(self.database_path)

    def test_digest_notification_state_is_backward_compatible_and_idempotent(self) -> None:
        self.assertFalse(self.store.has_digest_notification("2026-10-08"))
        self.store.mark_digest_notification(
            "2026-10-08", notified_at="2026-10-07T23:00:00+00:00"
        )
        self.store.mark_digest_notification(
            "2026-10-08", notified_at="2026-10-08T00:00:00+00:00"
        )
        self.assertTrue(self.store.has_digest_notification("2026-10-08"))
        connection = sqlite3.connect(self.database_path)
        count = connection.execute(
            "SELECT COUNT(*) FROM digest_notifications WHERE local_date = ?",
            ("2026-10-08",),
        ).fetchone()[0]
        connection.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
