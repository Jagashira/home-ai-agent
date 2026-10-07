"""Idempotent Home Assistant actions for actionable classified email."""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, TextIO
from zoneinfo import ZoneInfo

from app.integrations.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
)
from app.mail.classifier import EmailClassification
from app.mail.classify_recent import ClassifiedMessage
from app.storage.classification_store import ClassificationStore


TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(otp|one[- ]time(?: password| code)?|verification code|"
    r"authentication code|認証コード|ワンタイム(?:パスワード|コード)?|"
    r"password|pin|reset token|api key|secret)"
    r"(\s*(?:[:：=\-]|は|が)?\s*)[A-Za-z0-9_\-]{4,}"
)
_DECORATIVE_BRACKETS = str.maketrans("", "", "【】[]［］「」『』〈〉《》")
_DECORATIVE_EDGE_CHARACTERS = "★☆●○■□◆◇▲△▼▽※♪♬|｜"


@dataclass
class HomeAssistantActionStats:
    tasks_added: int = 0
    would_add_tasks: int = 0
    tasks_already_present: int = 0
    notifications_sent: int = 0
    would_send_notifications: int = 0
    notifications_already_sent: int = 0
    expired_skipped: int = 0
    superseded_skipped: int = 0
    failures: int = 0


def is_actionable(classification: EmailClassification) -> bool:
    return classification.action_required or classification.reply_required


def normalize_action_case_text(value: str, *, subject: bool = False) -> str:
    """Normalize exact case keys without fuzzy or semantic matching."""
    normalized = unicodedata.normalize("NFKC", value)
    if subject:
        normalized = normalized.translate(_DECORATIVE_BRACKETS).strip()
        normalized = normalized.strip(_DECORATIVE_EDGE_CHARACTERS).strip()
    return re.sub(r"\s+", " ", normalized).strip()


def _select_newest_actionable_messages(
    messages: list[ClassifiedMessage],
) -> tuple[list[ClassifiedMessage], int]:
    newest_by_case: dict[tuple[str, str], ClassifiedMessage] = {}
    actionable_count = 0
    for item in messages:
        if not is_actionable(item.classification):
            continue
        actionable_count += 1
        key = (
            normalize_action_case_text(item.classification.organization or ""),
            normalize_action_case_text(item.metadata["subject"], subject=True),
        )
        existing = newest_by_case.get(key)
        if (
            existing is None
            or item.metadata["received_at"] > existing.metadata["received_at"]
        ):
            newest_by_case[key] = item
    selected = sorted(
        newest_by_case.values(),
        key=lambda item: item.metadata["received_at"],
        reverse=True,
    )
    return selected, actionable_count - len(selected)


def _redact_sensitive_values(value: str) -> str:
    return _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[redacted]", value)


def action_title(item: ClassifiedMessage) -> str:
    organization = (item.classification.organization or "").strip()
    subject = _redact_sensitive_values(item.metadata["subject"].strip())
    if organization:
        return f"{organization} | {subject}"
    return subject


def _deadline_description(value: datetime | date | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def todo_description(item: ClassifiedMessage) -> str:
    classification = item.classification
    lines = [
        _redact_sensitive_values(classification.summary),
        f"Subject: {_redact_sensitive_values(item.metadata['subject'])}",
        f"Account: {item.metadata['account_id']}",
    ]
    deadline = _deadline_description(classification.deadline_at)
    if deadline is not None:
        lines.append(f"Deadline: {deadline}")
    return "\n".join(lines)


def notification_message(item: ClassifiedMessage) -> str:
    classification = item.classification
    lines = [action_title(item)]
    deadline = classification.deadline_at
    if isinstance(deadline, datetime):
        local_deadline = deadline.astimezone(TOKYO_TIMEZONE)
        lines.append(f"期限: {local_deadline:%m/%d %H:%M}")
    elif isinstance(deadline, date):
        lines.append(f"期限: {deadline:%m/%d}")
    lines.append(_redact_sensitive_values(classification.summary))
    return "\n".join(lines)


def process_home_assistant_actions(
    messages: list[ClassifiedMessage],
    store: ClassificationStore,
    *,
    dry_run: bool,
    stats: HomeAssistantActionStats,
    client_factory: Callable[[], HomeAssistantClient] = (
        HomeAssistantClient.from_environment
    ),
    error_stream: TextIO = sys.stderr,
    now: datetime | None = None,
) -> None:
    """Create each pending To-do and notification independently and once."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    selected_messages, superseded_count = _select_newest_actionable_messages(
        messages
    )
    stats.superseded_skipped += superseded_count
    client: HomeAssistantClient | None = None
    configuration_failed = False

    def get_client() -> HomeAssistantClient | None:
        nonlocal client, configuration_failed
        if client is not None:
            return client
        if configuration_failed:
            return None
        try:
            client = client_factory()
        except HomeAssistantError:
            configuration_failed = True
            print("Home Assistant configuration unavailable", file=error_stream)
            return None
        return client

    for item in selected_messages:
        deadline = item.classification.deadline_at
        if isinstance(deadline, datetime) and deadline < current_time:
            stats.expired_skipped += 1
            continue
        metadata = item.metadata
        state = store.get_mail_action_state(
            provider=metadata["provider"],
            account_id=metadata["account_id"],
            message_id=metadata["message_id"],
        )

        if state.ha_task_created_at is not None:
            stats.tasks_already_present += 1
        elif dry_run:
            stats.would_add_tasks += 1
        else:
            ha_client = get_client()
            if ha_client is None:
                stats.failures += 1
            else:
                try:
                    deadline = item.classification.deadline_at
                    due_datetime = (
                        deadline.isoformat()
                        if isinstance(deadline, datetime)
                        else None
                    )
                    ha_client.add_todo_item(
                        item=action_title(item),
                        description=todo_description(item),
                        due_datetime=due_datetime,
                    )
                    store.mark_ha_task_created(
                        provider=metadata["provider"],
                        account_id=metadata["account_id"],
                        message_id=metadata["message_id"],
                    )
                    stats.tasks_added += 1
                except Exception:
                    stats.failures += 1
                    print(
                        f"{metadata['account_id']} / message "
                        f"{metadata['message_id']}: Home Assistant To-do failed",
                        file=error_stream,
                    )

        if state.ha_notified_at is not None:
            stats.notifications_already_sent += 1
        elif dry_run:
            stats.would_send_notifications += 1
        else:
            ha_client = get_client()
            if ha_client is None:
                stats.failures += 1
            else:
                try:
                    ha_client.send_notification(
                        title="要対応メール",
                        message=notification_message(item),
                    )
                    store.mark_ha_notified(
                        provider=metadata["provider"],
                        account_id=metadata["account_id"],
                        message_id=metadata["message_id"],
                    )
                    stats.notifications_sent += 1
                except Exception:
                    stats.failures += 1
                    print(
                        f"{metadata['account_id']} / message "
                        f"{metadata['message_id']}: Home Assistant notification failed",
                        file=error_stream,
                    )
