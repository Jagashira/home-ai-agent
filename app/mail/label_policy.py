"""Deterministic Gmail label policy for validated classifications."""

from __future__ import annotations

from app.mail.classifier import EmailClassification


IMPORTANT_LABEL = "AI/重要"
ACTION_LABEL = "AI/要対応"
PROMOTION_LABEL = "AI/広告"

DOMAIN_LABELS = {
    "job": "AI/就活",
    "university_research": "AI/大学・研究",
    "purchase_billing": "AI/購入・請求",
    "security": "AI/セキュリティ",
    "service": "AI/サービス",
}

AI_LABEL_NAMES = frozenset(
    {
        IMPORTANT_LABEL,
        ACTION_LABEL,
        PROMOTION_LABEL,
        *DOMAIN_LABELS.values(),
    }
)


def labels_for_classification(classification: EmailClassification) -> set[str]:
    """Return the deterministic add-only label set for a classification."""
    labels: set[str] = set()
    if classification.importance >= 4:
        labels.add(IMPORTANT_LABEL)
    if classification.action_required or classification.reply_required:
        labels.add(ACTION_LABEL)

    domain_label = DOMAIN_LABELS.get(classification.domain)
    if domain_label is not None:
        labels.add(domain_label)
    if classification.mail_type == "promotion":
        labels.add(PROMOTION_LABEL)
    return labels
