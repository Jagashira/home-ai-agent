"""SQLite storage for cached email classifications."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "mail_agent.db"


@dataclass(frozen=True)
class MailActionState:
    ha_task_created_at: str | None = None
    ha_notified_at: str | None = None


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
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mail_actions (
                provider TEXT NOT NULL,
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                ha_task_created_at TEXT,
                ha_notified_at TEXT,
                PRIMARY KEY (provider, account_id, message_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS digest_notifications (
                local_date TEXT PRIMARY KEY,
                ha_notified_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def has_digest_notification(self, local_date: str) -> bool:
        """Return whether the morning digest succeeded for a local date."""
        row = self._connection.execute(
            "SELECT 1 FROM digest_notifications WHERE local_date = ?",
            (local_date,),
        ).fetchone()
        return row is not None

    def mark_digest_notification(
        self, local_date: str, *, notified_at: str | None = None
    ) -> None:
        """Persist morning digest delivery only after the external call succeeds."""
        timestamp = notified_at or datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT OR IGNORE INTO digest_notifications (local_date, ha_notified_at)
            VALUES (?, ?)
            """,
            (local_date, timestamp),
        )
        self._connection.commit()

    def get_mail_action_state(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
    ) -> MailActionState:
        """Return persisted Home Assistant success state for one message."""
        row = self._connection.execute(
            """
            SELECT ha_task_created_at, ha_notified_at
            FROM mail_actions
            WHERE provider = ? AND account_id = ? AND message_id = ?
            """,
            (provider, account_id, message_id),
        ).fetchone()
        if row is None:
            return MailActionState()
        return MailActionState(
            ha_task_created_at=row["ha_task_created_at"],
            ha_notified_at=row["ha_notified_at"],
        )

    def mark_ha_task_created(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
        created_at: str | None = None,
    ) -> None:
        """Persist To-do creation only after the external request succeeds."""
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO mail_actions (
                provider, account_id, message_id, ha_task_created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(provider, account_id, message_id) DO UPDATE SET
                ha_task_created_at = COALESCE(
                    mail_actions.ha_task_created_at,
                    excluded.ha_task_created_at
                )
            """,
            (provider, account_id, message_id, timestamp),
        )
        self._connection.commit()

    def mark_ha_notified(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
        notified_at: str | None = None,
    ) -> None:
        """Persist notification delivery only after the external request succeeds."""
        timestamp = notified_at or datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO mail_actions (
                provider, account_id, message_id, ha_notified_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(provider, account_id, message_id) DO UPDATE SET
                ha_notified_at = COALESCE(
                    mail_actions.ha_notified_at,
                    excluded.ha_notified_at
                )
            """,
            (provider, account_id, message_id, timestamp),
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

    def get_saved_classification(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
    ) -> str | None:
        """Return the saved classification for one provider account message."""
        row = self._connection.execute(
            """
            SELECT classification_json
            FROM mail_classifications
            WHERE provider = ?
              AND account_id = ?
              AND message_id = ?
            """,
            (provider, account_id, message_id),
        ).fetchone()
        return None if row is None else str(row["classification_json"])

    def get_current_classification(
        self,
        *,
        provider: str,
        account_id: str,
        message_id: str,
        model: str,
        classifier_version: str,
    ) -> str | None:
        """Return a saved result for an immutable message and classifier version."""
        row = self._connection.execute(
            """
            SELECT classification_json
            FROM mail_classifications
            WHERE provider = ?
              AND account_id = ?
              AND message_id = ?
              AND model = ?
              AND classifier_version = ?
            """,
            (provider, account_id, message_id, model, classifier_version),
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
