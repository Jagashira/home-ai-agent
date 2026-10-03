"""Unit tests for structured email classification."""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIStatusError, OpenAI
from pydantic import ValidationError

from app.ai.deepseek_client import (
    DeepSeekConfigurationError,
    format_deepseek_api_error,
    get_deepseek_client,
)
from app.mail.classifier import (
    CLASSIFICATION_JSON_SCHEMA,
    ClassificationResponseError,
    EmailClassification,
    classify_email,
    parse_classification_json,
)


def valid_classification(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "domain": "job",
        "sender_type": "direct_organization",
        "mail_type": "selection",
        "organization": "Example株式会社",
        "importance": 4,
        "action_required": True,
        "reply_required": False,
        "deadline_at": None,
        "event_at": None,
        "summary": "選考手続きに関するメールです。内容を確認してください。",
        "confidence": 0.9,
    }
    result.update(overrides)
    return result


class FakeResponses:
    def __init__(self, output: dict[str, object], status: str = "completed") -> None:
        self.output = output
        self.status = status
        self.request: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.request = kwargs
        return SimpleNamespace(
            status=self.status,
            output_text=json.dumps(self.output, ensure_ascii=False),
        )


class FakeClient:
    def __init__(self, output: dict[str, object], status: str = "completed") -> None:
        self.responses = FakeResponses(output, status)


class EmailClassificationTests(unittest.TestCase):
    def test_valid_schema_and_null_dates(self) -> None:
        result = parse_classification_json(json.dumps(valid_classification()))
        self.assertIsNone(result.deadline_at)
        self.assertIsNone(result.event_at)
        self.assertFalse(CLASSIFICATION_JSON_SCHEMA["additionalProperties"])

    def test_date_only_is_preserved_without_inventing_a_time(self) -> None:
        result = parse_classification_json(
            json.dumps(valid_classification(deadline_at="2026-10-03"))
        )
        self.assertEqual(result.model_dump(mode="json")["deadline_at"], "2026-10-03")

    def test_datetime_without_timezone_is_rejected(self) -> None:
        with self.assertRaises(ClassificationResponseError):
            parse_classification_json(
                json.dumps(
                    valid_classification(deadline_at="2026-10-03T23:55:00")
                )
            )

    def test_invalid_enum_is_rejected(self) -> None:
        with self.assertRaises(ClassificationResponseError):
            parse_classification_json(
                json.dumps(valid_classification(domain="not_a_domain"))
            )

    def test_importance_must_be_between_one_and_five(self) -> None:
        for value in (0, 6):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                EmailClassification.model_validate(
                    valid_classification(importance=value)
                )

    def test_confidence_must_be_between_zero_and_one(self) -> None:
        for value in (-0.01, 1.01):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                EmailClassification.model_validate(
                    valid_classification(confidence=value)
                )

    def test_invalid_json_and_extra_fields_are_rejected(self) -> None:
        with self.assertRaises(ClassificationResponseError):
            parse_classification_json("not json")
        with self.assertRaises(ClassificationResponseError):
            parse_classification_json(
                json.dumps(valid_classification(unexpected="value"))
            )

    def test_missing_api_key_has_clear_error(self) -> None:
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}),
            patch("app.ai.deepseek_client.load_dotenv"),
            self.assertRaisesRegex(DeepSeekConfigurationError, "DEEPSEEK_API_KEY"),
        ):
            get_deepseek_client()

    def test_responses_request_uses_schema_and_only_allowed_email_fields(self) -> None:
        client = FakeClient(valid_classification())
        result = classify_email(
            client,
            received_at="2026-10-03 10:05:00 Asia/Tokyo",
            sender="sender@example.com",
            subject="選考のご案内",
            normalized_body="明日までにフォームを入力してください。",
        )

        self.assertEqual(result.domain, "job")
        request = client.responses.request
        assert request is not None
        self.assertEqual(request["model"], "deepseek-flash")
        self.assertEqual(request["reasoning"], {"effort": "none"})
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertNotIn("strict", request["text"]["format"])
        sent_email = json.loads(request["input"])
        self.assertEqual(
            set(sent_email),
            {"received_at", "from", "subject", "body"},
        )
        self.assertNotIn("message_id", sent_email)

    def test_prompt_defines_sender_event_and_required_action_rules(self) -> None:
        client = FakeClient(valid_classification())
        classify_email(
            client,
            received_at="2026-10-03 10:05:00 Asia/Tokyo",
            sender=(
                "ソニーグループ インターンシップ／新卒採用事務局 "
                "<sony_group_2028@mypage-info.com>"
            ),
            subject="Sony Group Career Forum Autumn",
            normalized_body="任意参加の採用イベントです。",
        )

        request = client.responses.request
        assert request is not None
        instructions = " ".join(str(request["instructions"]).split())
        required_rules = (
            "substantive sender",
            "not merely the SMTP domain",
            "mypage-info.com",
            "sender_type=direct_organization",
            "mail_type=event",
            "Do not use promotion merely",
            "registration link or deadline alone does not make action_required true",
            "importance=3",
            "Direct delivery alone does not justify 4 or 5",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, instructions)

    def test_prompt_defines_deadline_security_and_service_regressions(self) -> None:
        client = FakeClient(valid_classification())
        classify_email(
            client,
            received_at="2026-10-03 10:05:00 Asia/Tokyo",
            sender="service@example.com",
            subject="Regression cases",
            normalized_body="Test body",
        )

        request = client.responses.request
        assert request is not None
        instructions = " ".join(str(request["instructions"]).split())
        regression_rules = {
            "earliest offer date is not a deadline": (
                '"最短10月10日に内定の可能性があります" means deadline_at=null'
            ),
            "reservation cutoff is a deadline": (
                '"予約締切は10月3日23:55" identifies a deadline'
            ),
            "authentication code is not copied": (
                'never include "350295" in summary'
            ),
            "unknown security action is conditional": (
                "自身で行った操作であれば認証し、心当たりがない場合は "
                "アカウントの安全性を確認する必要があります。"
            ),
            "service newsletter uses service domain": (
                "Unsplash voting or general newsletter should normally be "
                "domain=service"
            ),
        }
        for case, rule in regression_rules.items():
            with self.subTest(case=case):
                self.assertIn(rule, instructions)

    def test_api_error_diagnostics_include_only_safe_details(self) -> None:
        request = httpx.Request("POST", "https://api.deepseek.com/responses")
        response = httpx.Response(429, request=request)
        error = APIStatusError(
            "response body containing private content",
            response=response,
            body={"private": "email body"},
        )

        rendered = format_deepseek_api_error(error)
        self.assertEqual(
            rendered,
            "DeepSeek API request failed (APIStatusError, HTTP 429)",
        )
        self.assertNotIn("private", rendered)
        self.assertNotIn("email body", rendered)

    def test_openai_sdk_http_payload_does_not_include_strict(self) -> None:
        captured_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "resp_mock",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "model": "deepseek-flash",
                    "output": [
                        {
                            "type": "message",
                            "id": "msg_mock",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(valid_classification()),
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                    "usage": {
                        "input_tokens": 1,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 1,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 2,
                    },
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAI(
                api_key="test-key-not-real",
                base_url="https://api.deepseek.com",
                http_client=http_client,
            )
            classify_email(
                client,
                received_at="2026-10-03 10:05:00 Asia/Tokyo",
                sender="sender@example.com",
                subject="Subject",
                normalized_body="Body",
            )

        text_format = captured_payload["text"]["format"]
        self.assertNotIn("strict", text_format)

    def test_incomplete_response_is_rejected(self) -> None:
        client = FakeClient(valid_classification(), status="incomplete")
        with self.assertRaises(ClassificationResponseError):
            classify_email(
                client,
                received_at="2026-10-03 10:05:00 Asia/Tokyo",
                sender="sender@example.com",
                subject="Subject",
                normalized_body="Body",
            )


if __name__ == "__main__":
    unittest.main()
