"""SQLite storage for cached email classifications."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "mail_agent.db"


class ClassificationStore:
    """Persist classifications without storing message bodies or credentials."""

    def __init__(self, database_path: Path | str = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mail_classifications (
                provider TEXT NOT NULL,
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                from_address TEXT NOT NULL,
                subject TEXT NOT NULL,
                received_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                classification_json TEXT NOT NULL,
                model TEXT NOT NULL,
                classifier_version TEXT NOT NULL,
                classified_at TEXT NOT NULL,
                PRIMARY KEY (provider, account_id, message_id)
            )
            """
        )
        self._connection.commit()

    def get_cached_classification(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
        content_hash: str,
        model: str,
        classifier_version: str,
    ) -> str | None:
        """Return cached JSON only when every cache validity field matches."""
        row = self._connection.execute(
            """
            SELECT classification_json
            FROM mail_classifications
            WHERE provider = ?
              AND account_id = ?
              AND message_id = ?
              AND content_hash = ?
              AND model = ?
              AND classifier_version = ?
            """,
            (
                provider,
                account_id,
                message_id,
                content_hash,
                model,
                classifier_version,
            ),
        ).fetchone()
        return None if row is None else str(row["classification_json"])

    def save_classification(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
        thread_id: str,
        from_address: str,
        subject: str,
        received_at: str,
        content_hash: str,
        classification_json: str,
        model: str,
        classifier_version: str,
        classified_at: str | None = None,
    ) -> None:
        """Insert or replace the current cached classification for one message."""
        timestamp = classified_at or datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO mail_classifications (
                provider,
                account_id,
                message_id,
                thread_id,
                from_address,
                subject,
                received_at,
                content_hash,
                classification_json,
                model,
                classifier_version,
                classified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, account_id, message_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                from_address = excluded.from_address,
                subject = excluded.subject,
                received_at = excluded.received_at,
                content_hash = excluded.content_hash,
                classification_json = excluded.classification_json,
                model = excluded.model,
                classifier_version = excluded.classifier_version,
                classified_at = excluded.classified_at
            """,
            (
                provider,
                account_id,
                message_id,
                thread_id,
                from_address,
                subject,
                received_at,
                content_hash,
                classification_json,
                model,
                classifier_version,
                timestamp,
            ),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ClassificationStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
