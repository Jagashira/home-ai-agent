"""Safe deterministic text handling for external notifications."""

from __future__ import annotations

import re


_SENSITIVE_LABEL = (
    r"otp|one[- ]time(?: password| code)?|verification code|"
    r"authentication code|認証コード|ワンタイム(?:パスワード|コード)?|"
    r"password|pin|reset token|oauth token|access token|refresh token|"
    r"api key|secret"
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    rf"(?i)({_SENSITIVE_LABEL})"
    r"(\s*(?:[:：=\-]|は|が)?\s*)[A-Za-z0-9_\-]{4,}"
)
_NUMERIC_CODE_BEFORE_LABEL_PATTERN = re.compile(
    rf"(?i)\b\d{{4,8}}\b(?=[^\n]{{0,24}}(?:otp|verification code|"
    r"authentication code|認証コード|ワンタイム(?:パスワード|コード)?|pin))"
)


def redact_sensitive_values(value: str) -> str:
    """Remove likely authentication values while retaining surrounding context."""
    redacted = _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[redacted]", value)
    return _NUMERIC_CODE_BEFORE_LABEL_PATTERN.sub("[redacted]", redacted)
