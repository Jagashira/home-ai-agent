"""DeepSeek client configuration through the OpenAI Python SDK."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"


class DeepSeekConfigurationError(RuntimeError):
    """Raised when the local DeepSeek configuration is incomplete."""


def format_deepseek_api_error(error: OpenAIError) -> str:
    """Return safe diagnostics without including request or response contents."""
    error_type = type(error).__name__
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return f"DeepSeek API request failed ({error_type}, HTTP {status_code})"
    return f"DeepSeek API request failed ({error_type})"


def get_deepseek_client(api_key: str | None = None) -> OpenAI:
    """Return a DeepSeek Responses API client without exposing its API key."""
    load_dotenv()
    resolved_api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not resolved_api_key or not resolved_api_key.strip():
        raise DeepSeekConfigurationError("DEEPSEEK_API_KEY is not set")

    return OpenAI(
        api_key=resolved_api_key.strip(),
        base_url=DEEPSEEK_BASE_URL,
    )
