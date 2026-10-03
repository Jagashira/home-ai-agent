from __future__ import annotations

import unittest

from app.mail.classifier import EmailClassification
from app.mail.label_policy import labels_for_classification


def classification(**overrides: object) -> EmailClassification:
    data: dict[str, object] = {
        "domain": "other",
        "sender_type": "unknown",
        "mail_type": "information",
        "organization": None,
        "importance": 3,
        "action_required": False,
        "reply_required": False,
        "deadline_at": None,
        "event_at": None,
        "summary": "テスト分類です。",
        "confidence": 0.9,
    }
    data.update(overrides)
    return EmailClassification.model_validate(data)


class LabelPolicyTests(unittest.TestCase):
    def test_importance_four_is_important(self) -> None:
        self.assertIn("AI/重要", labels_for_classification(classification(importance=4)))

    def test_importance_three_is_not_important(self) -> None:
        self.assertNotIn("AI/重要", labels_for_classification(classification()))

    def test_action_required_needs_action(self) -> None:
        self.assertIn(
            "AI/要対応",
            labels_for_classification(classification(action_required=True)),
        )

    def test_reply_required_needs_action(self) -> None:
        self.assertIn(
            "AI/要対応",
            labels_for_classification(classification(reply_required=True)),
        )

    def test_deadline_alone_does_not_need_action(self) -> None:
        result = labels_for_classification(
            classification(deadline_at="2026-10-05T12:00:00+09:00")
        )
        self.assertNotIn("AI/要対応", result)

    def test_job_domain(self) -> None:
        self.assertEqual(
            labels_for_classification(classification(domain="job")), {"AI/就活"}
        )

    def test_university_research_domain(self) -> None:
        self.assertEqual(
            labels_for_classification(classification(domain="university_research")),
            {"AI/大学・研究"},
        )

    def test_purchase_billing_domain(self) -> None:
        self.assertEqual(
            labels_for_classification(classification(domain="purchase_billing")),
            {"AI/購入・請求"},
        )

    def test_security_domain(self) -> None:
        self.assertEqual(
            labels_for_classification(classification(domain="security")),
            {"AI/セキュリティ"},
        )

    def test_service_domain(self) -> None:
        self.assertEqual(
            labels_for_classification(classification(domain="service")),
            {"AI/サービス"},
        )

    def test_promotion_mail_type(self) -> None:
        self.assertEqual(
            labels_for_classification(classification(mail_type="promotion")),
            {"AI/広告"},
        )

    def test_multiple_conditions_produce_multiple_labels(self) -> None:
        result = labels_for_classification(
            classification(
                domain="job",
                mail_type="promotion",
                importance=5,
                action_required=True,
            )
        )
        self.assertEqual(result, {"AI/重要", "AI/要対応", "AI/就活", "AI/広告"})


if __name__ == "__main__":
    unittest.main()
