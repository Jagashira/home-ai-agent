"""CLI for a manual Home Assistant iPhone notification test."""

from __future__ import annotations

import sys

from app.integrations.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
)


DEFAULT_TITLE = "Home AI Agent"
DEFAULT_MESSAGE = "Home Mail Agent Python接続テスト"


def main() -> int:
    try:
        client = HomeAssistantClient.from_environment()
    except HomeAssistantError as error:
        print(f"Home Assistant configuration: FAILED ({error})", file=sys.stderr)
        return 1

    try:
        client.check_api()
    except HomeAssistantError as error:
        print(f"Home Assistant API: FAILED ({error})", file=sys.stderr)
        return 1
    print("Home Assistant API: OK")

    try:
        client.send_notification(title=DEFAULT_TITLE, message=DEFAULT_MESSAGE)
    except HomeAssistantError as error:
        print(f"Notification sent: FAILED ({error})", file=sys.stderr)
        return 1
    print("Notification sent: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
