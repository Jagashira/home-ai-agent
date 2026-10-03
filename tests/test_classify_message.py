"""Tests for the one-message classification CLI flow."""

from __future__ import annotations

import base64
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from app.mail import classify_message
from app.mail.classifier import EmailClassification


class ClassifyMessageTests(unittest.TestCase):
    def test_success_prints_only_classification_json(self) -> None:
        body = "明日までに\u00a0フォームを入力してください。\u200b"
        encoded_body = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        message = {
            "internalDate": "1791066600000",
            "payload": {
                "mimeType": "text/plain",
                "filename": "",
                "headers": [
                    {"name": "From", "value": "company@example.com"},
                    {"name": "Subject", "value": "選考手続き"},
                    {
                        "name": "Content-Type",
                        "value": "text/plain; charset=utf-8",
                    },
                ],
                "body": {"data": encoded_body},
            },
        }
        classification = EmailClassification.model_validate(
            {
                "domain": "job",
                "sender_type": "direct_organization",
                "mail_type": "action_required",
                "organization": "Example株式会社",
                "importance": 4,
                "action_required": True,
                "reply_required": False,
                "deadline_at": None,
                "event_at": None,
                "summary": "選考手続きの案内です。期限までにフォーム入力が必要です。",
                "confidence": 0.9,
            }
        )
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(
                classify_message,
                "parse_args",
                return_value=SimpleNamespace(
                    account_id="google_2",
                    message_id="private-message-id",
                ),
            ),
            patch.object(classify_message, "get_gmail_service", return_value=object()),
            patch.object(classify_message, "get_full_message", return_value=message),
            patch.object(classify_message, "get_deepseek_client", return_value=object()),
            patch.object(
                classify_message,
                "classify_email",
                return_value=classification,
            ) as classify_mock,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(classify_message.main(), 0)

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["domain"], "job")
        self.assertEqual(stderr.getvalue(), "")
        sent_fields = classify_mock.call_args.kwargs
        self.assertEqual(
            sent_fields["normalized_body"],
            "明日までに フォームを入力してください。",
        )
        self.assertNotIn("private-message-id", str(sent_fields))


if __name__ == "__main__":
    unittest.main()
