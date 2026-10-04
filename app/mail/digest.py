"""Deterministic daily digest generation from validated classifications."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
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


@dataclass
class DigestEntry:
    """One displayed digest entry, possibly shared by multiple accounts."""

    item: ClassifiedMessage
    account_ids: list[str] = field(default_factory=list)


def _aggregate_cross_account_duplicates(
    messages: Sequence[ClassifiedMessage],
) -> list[DigestEntry]:
    entries_by_key: dict[tuple[str | None, str, str], list[DigestEntry]] = {}
    entries: list[DigestEntry] = []
    for item in sorted(
        messages,
        key=lambda value: value.metadata["received_at"],
        reverse=True,
    ):
        key = (
            item.classification.organization,
            item.metadata["subject"],
            item.classification.summary,
        )
        account_id = item.metadata["account_id"]
        matching_entries = entries_by_key.setdefault(key, [])
        entry = next(
            (
                candidate
                for candidate in matching_entries
                if account_id not in candidate.account_ids
            ),
            None,
        )
        if entry is None:
            entry = DigestEntry(item=item, account_ids=[account_id])
            matching_entries.append(entry)
            entries.append(entry)
        else:
            entry.account_ids.append(account_id)
    return entries


def _section_for(entry: DigestEntry) -> str:
    classification = entry.item.classification
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
    entry: DigestEntry,
    *,
    show_deadline: bool = False,
) -> None:
    item = entry.item
    classification = item.classification
    sender = classification.organization or item.metadata["from"]
    deadline = _format_deadline(classification.deadline_at)
    deadline_text = f"{deadline} " if show_deadline else ""
    lines.append(f"- {deadline_text}{sender} | {item.metadata['subject']}")
    lines.append(f"  {classification.summary}")
    if len(entry.account_ids) > 1:
        lines.append(f"  Accounts: {', '.join(sorted(entry.account_ids))}")


def _append_job_section(
    lines: list[str],
    entries: list[DigestEntry],
) -> None:
    priority: list[DigestEntry] = []
    general_platform: list[DigestEntry] = []
    other: list[DigestEntry] = []
    priority_mail_types = {
        "selection",
        "deadline",
        "result",
        "action_required",
        "event",
    }
    for entry in entries:
        item = entry.item
        classification = item.classification
        if (
            classification.action_required
            or classification.reply_required
            or classification.importance >= 3
            or classification.mail_type in priority_mail_types
        ):
            priority.append(entry)
        elif (
            classification.sender_type == "platform"
            and classification.importance <= 2
        ):
            general_platform.append(entry)
        else:
            other.append(entry)

    displayed = [*priority, *general_platform[:3]]
    hidden_count = len(other) + max(0, len(general_platform) - 3)
    for entry in displayed:
        _append_message(lines, entry)
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
    sections: dict[str, list[DigestEntry]] = {
        name: [] for name in (*SECTION_ORDER, "広告")
    }
    for entry in _aggregate_cross_account_duplicates(messages):
        sections[_section_for(entry)].append(entry)

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
        for entry in items:
            _append_message(
                lines,
                entry,
                show_deadline=section_name in {"要対応期限", "参考期限"},
            )

    if sections["広告"]:
        lines.extend(("", "[広告]", f"{len(sections['広告'])}件"))
    if unclassified_count:
        lines.extend(("", f"未分類メール: {unclassified_count}件"))
    return "\n".join(lines)
