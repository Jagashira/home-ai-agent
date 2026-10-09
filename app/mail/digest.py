"""Deterministic daily digest generation from validated classifications."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.mail.case_normalizer import normalize_case_text
from app.mail.classify_recent import ClassifiedMessage
from app.mail.safe_text import redact_sensitive_values


TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")
SECTION_ORDER = (
    "要対応", "要確認", "要対応期限", "大学・研究", "就活",
    "購入・請求", "セキュリティ", "参考期限", "その他",
)


@dataclass
class DigestEntry:
    """One digest entry, possibly shared by multiple accounts."""

    item: ClassifiedMessage
    account_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedDigest:
    """Preprocessed entries and counters shared by digest formatters."""

    entries: list[DigestEntry]
    duplicate_collapsed: int
    expired_suppressed: int


def prepare_digest_entries(
    messages: Sequence[ClassifiedMessage], *, now: datetime
) -> PreparedDigest:
    """Collapse exact same cases, retain newest, and suppress expired deadlines."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    entries_by_key: dict[tuple[str, str], DigestEntry] = {}
    duplicate_collapsed = 0
    for item in sorted(
        messages, key=lambda value: value.metadata["received_at"], reverse=True
    ):
        key = (
            normalize_case_text(item.classification.organization or ""),
            normalize_case_text(item.metadata["subject"], subject=True),
        )
        account_id = item.metadata["account_id"]
        entry = entries_by_key.get(key)
        if entry is None:
            entries_by_key[key] = DigestEntry(item=item, account_ids=[account_id])
        else:
            duplicate_collapsed += 1
            if account_id not in entry.account_ids:
                entry.account_ids.append(account_id)

    entries: list[DigestEntry] = []
    expired_suppressed = 0
    for entry in entries_by_key.values():
        deadline = entry.item.classification.deadline_at
        if isinstance(deadline, datetime) and deadline < now:
            expired_suppressed += 1
        else:
            entries.append(entry)
    return PreparedDigest(entries, duplicate_collapsed, expired_suppressed)


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


def format_digest_deadline(value: datetime | date | None) -> str:
    if isinstance(value, datetime):
        return value.astimezone(TOKYO_TIMEZONE).strftime("%m/%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%m/%d")
    return ""


def _format_period(value: datetime) -> str:
    return value.astimezone(TOKYO_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def _append_message(
    lines: list[str], entry: DigestEntry, *, show_deadline: bool = False
) -> None:
    item = entry.item
    classification = item.classification
    sender = classification.organization or item.metadata["from"]
    deadline = format_digest_deadline(classification.deadline_at)
    lines.append(
        f"- {deadline + ' ' if show_deadline else ''}{sender} | "
        f"{redact_sensitive_values(item.metadata['subject'])}"
    )
    lines.append(f"  {redact_sensitive_values(classification.summary)}")
    if len(entry.account_ids) > 1:
        lines.append(f"  Accounts: {', '.join(sorted(entry.account_ids))}")


def _append_job_section(lines: list[str], entries: list[DigestEntry]) -> None:
    exempt: list[DigestEntry] = []
    ordinary: list[DigestEntry] = []
    for entry in entries:
        classification = entry.item.classification
        if (
            classification.action_required
            or classification.reply_required
            or classification.mail_type in {"selection", "result", "action_required"}
        ):
            exempt.append(entry)
        else:
            ordinary.append(entry)

    sender_priority = {"direct_organization": 0, "platform": 1}
    ordinary.sort(
        key=lambda entry: (
            -entry.item.classification.importance,
            sender_priority.get(entry.item.classification.sender_type, 2),
            -entry.item.metadata["received_at"].timestamp(),
            entry.item.metadata["account_id"],
            entry.item.metadata["message_id"],
        )
    )
    for entry in [*exempt, *ordinary[:5]]:
        _append_message(lines, entry)
    hidden_count = max(0, len(ordinary) - 5)
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
    """Build a concise terminal digest without an additional AI call."""
    prepared = prepare_digest_entries(messages, now=period_end)
    sections: dict[str, list[DigestEntry]] = {
        name: [] for name in (*SECTION_ORDER, "広告")
    }
    for entry in prepared.entries:
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
        f"Duplicate digest messages collapsed: {prepared.duplicate_collapsed}",
        f"Expired digest deadlines suppressed: {prepared.expired_suppressed}",
    ]
    for section_name in SECTION_ORDER:
        items = sections[section_name]
        if not items:
            continue
        lines.extend(("", f"[{section_name}]"))
        if section_name == "就活":
            _append_job_section(lines, items)
        else:
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
