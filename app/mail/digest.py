"""Deterministic daily digest generation from validated classifications."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.mail.classify_recent import ClassifiedMessage


TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")
SECTION_ORDER = (
    "要対応",
    "要確認",
    "要対応期限",
    "大学・研究",
    "就活",
    "購入・請求",
    "セキュリティ",
    "参考期限",
    "その他",
)


def _section_for(item: ClassifiedMessage) -> str:
    classification = item.classification
    if classification.deadline_at is not None:
        if classification.action_required or classification.reply_required:
            return "要対応期限"
        return "参考期限"
    if classification.action_required or classification.reply_required:
        return "要対応"
    if classification.importance >= 4:
        return "要確認"
    if classification.mail_type == "promotion":
        return "広告"
    return {
        "university_research": "大学・研究",
        "job": "就活",
        "purchase_billing": "購入・請求",
        "security": "セキュリティ",
    }.get(classification.domain, "その他")


def _format_deadline(value: datetime | date | None) -> str:
    if isinstance(value, datetime):
        return value.astimezone(TOKYO_TIMEZONE).strftime("%m/%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%m/%d")
    return ""


def _format_period(value: datetime) -> str:
    return value.astimezone(TOKYO_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def _append_message(
    lines: list[str],
    item: ClassifiedMessage,
    *,
    show_deadline: bool = False,
) -> None:
    classification = item.classification
    sender = classification.organization or item.metadata["from"]
    deadline = _format_deadline(classification.deadline_at)
    deadline_text = f"{deadline} " if show_deadline else ""
    lines.append(f"- {deadline_text}{sender} | {item.metadata['subject']}")
    lines.append(f"  {classification.summary}")


def _append_job_section(
    lines: list[str],
    items: list[ClassifiedMessage],
) -> None:
    priority: list[ClassifiedMessage] = []
    general_platform: list[ClassifiedMessage] = []
    other: list[ClassifiedMessage] = []
    for item in items:
        classification = item.classification
        if (
            classification.importance >= 3
            or classification.sender_type == "direct_organization"
            or classification.action_required
            or classification.reply_required
        ):
            priority.append(item)
        elif (
            classification.sender_type == "platform"
            and classification.importance <= 2
        ):
            general_platform.append(item)
        else:
            other.append(item)

    displayed = [*priority, *other, *general_platform[:3]]
    hidden_count = max(0, len(general_platform) - 3)
    for item in displayed:
        _append_message(lines, item)
    if hidden_count:
        lines.append(f"その他の就活案内: {hidden_count}件")


def generate_daily_digest(
    messages: Sequence[ClassifiedMessage],
    *,
    period_start: datetime,
    period_end: datetime,
    accounts: int,
    messages_found: int,
    new_classifications: int,
    cache_hits: int,
) -> str:
    """Build a concise digest without calling an AI service."""
    sections: dict[str, list[ClassifiedMessage]] = {
        name: [] for name in (*SECTION_ORDER, "広告")
    }
    for item in sorted(
        messages,
        key=lambda value: value.metadata["received_at"],
        reverse=True,
    ):
        sections[_section_for(item)].append(item)

    classified_count = len(messages)
    unclassified_count = max(0, messages_found - classified_count)
    lines = [
        "=== Daily Mail Digest ===",
        f"Period: {_format_period(period_start)} - {_format_period(period_end)}",
        f"Accounts: {accounts}",
        f"Messages found: {messages_found}",
        f"Classified: {classified_count}",
        f"Skipped/unclassified: {unclassified_count}",
        f"New classifications: {new_classifications}",
        f"Cache hits: {cache_hits}",
    ]

    for section_name in SECTION_ORDER:
        items = sections[section_name]
        if not items:
            continue
        lines.extend(("", f"[{section_name}]"))
        if section_name == "就活":
            _append_job_section(lines, items)
            continue
        for item in items:
            _append_message(
                lines,
                item,
                show_deadline=section_name in {"要対応期限", "参考期限"},
            )

    if sections["広告"]:
        lines.extend(("", "[広告]", f"{len(sections['広告'])}件"))
    if unclassified_count:
        lines.extend(("", f"未分類メール: {unclassified_count}件"))
    return "\n".join(lines)
