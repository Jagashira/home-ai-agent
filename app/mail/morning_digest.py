"""Concise deterministic Home Assistant morning digest notifications."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TextIO

from app.integrations.home_assistant.client import HomeAssistantClient
from app.mail.classify_recent import ClassifiedMessage
from app.mail.digest import (
    TOKYO_TIMEZONE,
    format_digest_deadline,
    prepare_digest_entries,
)
from app.mail.safe_text import redact_sensitive_values
from app.storage.classification_store import ClassificationStore


DEFAULT_ACTION_DETAIL_LIMIT = 5
DEFAULT_NOTIFICATION_LENGTH = 900


@dataclass(frozen=True)
class MorningDigest:
    title: str
    body: str


@dataclass
class MorningDigestNotificationStats:
    sent: int = 0
    already_sent: int = 0
    would_send: int = 0
    failures: int = 0
    preview: MorningDigest | None = None


def _short(value: str, limit: int = 44) -> str:
    safe = " ".join(redact_sensitive_values(value).split())
    return safe if len(safe) <= limit else f"{safe[: limit - 1]}…"


def _action_sort_key(item: ClassifiedMessage) -> tuple[int, datetime, str]:
    deadline = item.classification.deadline_at
    if isinstance(deadline, datetime):
        return (0, deadline, item.metadata["subject"])
    return (1, datetime.max.replace(tzinfo=TOKYO_TIMEZONE), item.metadata["subject"])


def format_morning_digest(
    messages: list[ClassifiedMessage],
    *,
    messages_found: int,
    now: datetime,
    action_detail_limit: int = DEFAULT_ACTION_DETAIL_LIMIT,
    max_length: int = DEFAULT_NOTIFICATION_LENGTH,
) -> MorningDigest:
    """Format a short notification from classification data only."""
    prepared = prepare_digest_entries(messages, now=now)
    items = [entry.item for entry in prepared.entries]
    actionable = sorted(
        [
            item
            for item in items
            if item.classification.action_required or item.classification.reply_required
        ],
        key=_action_sort_key,
    )
    lines = [f"📨 新着 {messages_found}件", "", f"⚠️ 要対応 {len(actionable)}件"]
    for item in actionable[:action_detail_limit]:
        classification = item.classification
        organization = classification.organization or item.metadata["from"]
        deadline = format_digest_deadline(classification.deadline_at)
        detail = deadline or _short(item.metadata["subject"])
        lines.append(f"・{_short(organization, 24)} | {detail}")
    if len(actionable) > action_detail_limit:
        lines.append(f"ほか {len(actionable) - action_detail_limit}件")

    counts = (
        ("👀 要確認", sum(item.classification.importance >= 4 for item in items)),
        (
            "🎓 大学・研究",
            sum(item.classification.domain == "university_research" for item in items),
        ),
        ("💼 就活", sum(item.classification.domain == "job" for item in items)),
        (
            "💳 購入・請求",
            sum(item.classification.domain == "purchase_billing" for item in items),
        ),
        ("🔐 セキュリティ", sum(item.classification.domain == "security" for item in items)),
        ("📢 広告", sum(item.classification.mail_type == "promotion" for item in items)),
    )
    lines.append("")
    lines.extend(f"{label} {count}件" for label, count in counts)
    body = "\n".join(lines)
    if len(body) > max_length:
        body = f"{body[: max(0, max_length - 1)].rstrip()}…"
    local_now = now.astimezone(TOKYO_TIMEZONE)
    return MorningDigest(f"朝のメールまとめ | {local_now:%m/%d}", body)


def process_morning_digest_notification(
    messages: list[ClassifiedMessage],
    store: ClassificationStore,
    *,
    messages_found: int,
    requested: bool,
    dry_run: bool,
    stats: MorningDigestNotificationStats,
    now: datetime,
    client_factory: Callable[[], HomeAssistantClient] = (
        HomeAssistantClient.from_environment
    ),
    error_stream: TextIO = sys.stderr,
) -> None:
    """Optionally send one morning digest per Tokyo calendar date."""
    if not requested:
        return
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    digest = format_morning_digest(messages, messages_found=messages_found, now=now)
    local_date = now.astimezone(TOKYO_TIMEZONE).date().isoformat()
    if store.has_digest_notification(local_date):
        stats.already_sent += 1
        if dry_run:
            stats.preview = digest
        return
    if dry_run:
        stats.would_send += 1
        stats.preview = digest
        return
    try:
        client_factory().send_notification(title=digest.title, message=digest.body)
        store.mark_digest_notification(local_date)
        stats.sent += 1
    except Exception:
        stats.failures += 1
        print("Morning digest notification failed", file=error_stream)
