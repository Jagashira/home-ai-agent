"""Home Assistant REST API integration."""

from app.integrations.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantConfigurationError,
    HomeAssistantError,
)

__all__ = [
    "HomeAssistantClient",
    "HomeAssistantConfigurationError",
    "HomeAssistantError",
]
