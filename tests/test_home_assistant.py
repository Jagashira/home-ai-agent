from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import requests

from app.integrations.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantConfigurationError,
    HomeAssistantError,
)
from app.integrations.home_assistant.test_notification import main


TEST_TOKEN = "private-test-token-value"


def response(status_code: int, payload: object | None = None) -> Mock:
    result = Mock()
    result.status_code = status_code
    result.json.return_value = payload
    return result


def client(session: Mock) -> HomeAssistantClient:
    return HomeAssistantClient(
        base_url="http://homeassistant.local:8123/",
        token=TEST_TOKEN,
        notify_service="mobile_app_satoshi_iphone",
        session=session,
    )


class HomeAssistantClientTests(unittest.TestCase):
    def test_successful_api_check(self) -> None:
        session = Mock()
        session.request.return_value = response(200, {"message": "API running."})

        client(session).check_api()

        session.request.assert_called_once_with(
            "GET",
            "http://homeassistant.local:8123/api/",
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
            json=None,
            timeout=10.0,
        )

    def test_successful_notification_and_service_url(self) -> None:
        session = Mock()
        session.request.return_value = response(200, [])
        home_assistant = client(session)

        home_assistant.send_notification(title="Title", message="Message")

        self.assertEqual(
            home_assistant.notification_url,
            "http://homeassistant.local:8123/api/services/notify/"
            "mobile_app_satoshi_iphone",
        )
        self.assertNotIn(TEST_TOKEN, home_assistant.notification_url)
        session.request.assert_called_once_with(
            "POST",
            home_assistant.notification_url,
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"title": "Title", "message": "Message"},
            timeout=10.0,
        )

    def test_missing_environment_variable(self) -> None:
        with self.assertRaises(HomeAssistantConfigurationError) as context:
            HomeAssistantClient.from_environment(
                {
                    "HOME_ASSISTANT_URL": "http://homeassistant.local:8123",
                    "HOME_ASSISTANT_TOKEN": TEST_TOKEN,
                }
            )
        self.assertIn("HOME_ASSISTANT_NOTIFY_SERVICE", str(context.exception))
        self.assertNotIn(TEST_TOKEN, str(context.exception))

    def test_unauthorized_response_is_safe(self) -> None:
        session = Mock()
        session.request.return_value = response(401)
        home_assistant = client(session)

        with self.assertRaises(HomeAssistantError) as context:
            home_assistant.check_api()

        self.assertIn("HTTP 401", str(context.exception))
        self.assertNotIn(TEST_TOKEN, str(context.exception))
        self.assertNotIn(TEST_TOKEN, repr(home_assistant))

    def test_timeout_and_connection_failure_are_safe(self) -> None:
        for failure, expected in (
            (requests.Timeout(f"timeout {TEST_TOKEN}"), "timed out"),
            (requests.ConnectionError(f"connection {TEST_TOKEN}"), "Could not connect"),
        ):
            with self.subTest(failure=type(failure).__name__):
                session = Mock()
                session.request.side_effect = failure
                with self.assertRaises(HomeAssistantError) as context:
                    client(session).check_api()
                self.assertIn(expected, str(context.exception))
                self.assertNotIn(TEST_TOKEN, str(context.exception))

    def test_cli_output_never_contains_token(self) -> None:
        configured_client = Mock()
        configured_client.check_api.side_effect = HomeAssistantError(
            "Could not connect to Home Assistant"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch(
                "app.integrations.home_assistant.test_notification."
                "HomeAssistantClient.from_environment",
                return_value=configured_client,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertNotIn(TEST_TOKEN, stdout.getvalue())
        self.assertNotIn(TEST_TOKEN, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
