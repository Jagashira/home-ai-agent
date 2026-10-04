"""Minimal, reusable Home Assistant REST API client."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv


DEFAULT_TIMEOUT_SECONDS = 10.0
_NOTIFY_SERVICE_PATTERN = re.compile(r"[a-z0-9_]+")
_TODO_ENTITY_PATTERN = re.compile(r"todo\.[a-z0-9_]+")


class HomeAssistantError(RuntimeError):
    """Raised for safe, user-facing Home Assistant failures."""


class HomeAssistantConfigurationError(HomeAssistantError):
    """Raised when required Home Assistant configuration is unavailable."""


class HomeAssistantClient:
    """Call the limited Home Assistant endpoints used by this application."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        notify_service: str,
        todo_entity: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        resolved_url = base_url.strip().rstrip("/")
        parsed_url = urlsplit(resolved_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise HomeAssistantConfigurationError(
                "HOME_ASSISTANT_URL must be a valid HTTP(S) base URL"
            )
        if not token.strip():
            raise HomeAssistantConfigurationError("HOME_ASSISTANT_TOKEN is not set")

        resolved_service = notify_service.strip()
        if not _NOTIFY_SERVICE_PATTERN.fullmatch(resolved_service):
            raise HomeAssistantConfigurationError(
                "HOME_ASSISTANT_NOTIFY_SERVICE is invalid"
            )
        resolved_todo_entity = todo_entity.strip()
        if not _TODO_ENTITY_PATTERN.fullmatch(resolved_todo_entity):
            raise HomeAssistantConfigurationError(
                "HOME_ASSISTANT_TODO_ENTITY is invalid"
            )
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self.base_url = resolved_url
        self.notify_service = resolved_service
        self.todo_entity = resolved_todo_entity
        self.timeout = timeout
        self._token = token.strip()
        self._session = session or requests.Session()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, "
            f"notify_service={self.notify_service!r}, "
            f"todo_entity={self.todo_entity!r}, timeout={self.timeout!r})"
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> HomeAssistantClient:
        """Build a client from environment variables or the existing .env file."""
        if environment is None:
            load_dotenv()
            environment = os.environ

        names = (
            "HOME_ASSISTANT_URL",
            "HOME_ASSISTANT_TOKEN",
            "HOME_ASSISTANT_NOTIFY_SERVICE",
            "HOME_ASSISTANT_TODO_ENTITY",
        )
        missing = [name for name in names if not environment.get(name, "").strip()]
        if missing:
            raise HomeAssistantConfigurationError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )
        return cls(
            base_url=environment["HOME_ASSISTANT_URL"],
            token=environment["HOME_ASSISTANT_TOKEN"],
            notify_service=environment["HOME_ASSISTANT_NOTIFY_SERVICE"],
            todo_entity=environment["HOME_ASSISTANT_TODO_ENTITY"],
            timeout=timeout,
            session=session,
        )

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/api/"

    @property
    def notification_url(self) -> str:
        return (
            f"{self.base_url}/api/services/notify/{self.notify_service}"
        )

    @property
    def todo_url(self) -> str:
        return f"{self.base_url}/api/services/todo/add_item"

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, str] | None = None,
    ) -> requests.Response:
        try:
            response = self._session.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=json,
                timeout=self.timeout,
            )
        except requests.Timeout as error:
            raise HomeAssistantError("Home Assistant request timed out") from None
        except requests.ConnectionError as error:
            raise HomeAssistantError("Could not connect to Home Assistant") from None
        except requests.RequestException as error:
            raise HomeAssistantError(
                f"Home Assistant request failed ({type(error).__name__})"
            ) from None

        if response.status_code in {401, 403}:
            raise HomeAssistantError(
                f"Home Assistant authentication failed (HTTP {response.status_code})"
            )
        if not 200 <= response.status_code < 300:
            raise HomeAssistantError(
                f"Home Assistant request failed (HTTP {response.status_code})"
            )
        return response

    def check_api(self) -> None:
        """Verify that the Home Assistant API is reachable and running."""
        response = self._request("GET", self.health_url)
        try:
            payload: Any = response.json()
        except (requests.JSONDecodeError, ValueError):
            raise HomeAssistantError(
                "Home Assistant API returned an invalid health response"
            ) from None
        if not isinstance(payload, dict) or payload.get("message") != "API running.":
            raise HomeAssistantError(
                "Home Assistant API returned an unexpected health response"
            )

    def send_notification(self, *, title: str, message: str) -> None:
        """Send one notification through the configured notify service."""
        self._request(
            "POST",
            self.notification_url,
            json={"title": title, "message": message},
        )

    def add_todo_item(
        self,
        *,
        item: str,
        description: str,
        due_datetime: str | None = None,
    ) -> None:
        """Create one item in the configured Home Assistant To-do list."""
        payload = {
            "entity_id": self.todo_entity,
            "item": item,
            "description": description,
        }
        if due_datetime is not None:
            payload["due_datetime"] = due_datetime
        self._request("POST", self.todo_url, json=payload)
