"""Safe deterministic text handling for external notifications."""

from __future__ import annotations

import re


REDACTION_MARKER = "[redacted]"
_SENSITIVE_LABEL = (
    r"confirmation code|verification code|authentication code|security code|"
    r"otp|one[- ]time(?: passcode| password| code)?|"
    r"認証コード|確認コード|ワンタイム(?:パスワード|コード)?|"
    r"password|pin|reset token|oauth token|access token|refresh token|"
    r"api key|secret"
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    rf"(?i)({_SENSITIVE_LABEL})"
    r"(\s*(?:(?:is|[:：=\-]|は|が)\s*)?)[A-Za-z0-9_\-]{4,}"
)
_NUMERIC_CODE_BEFORE_LABEL_PATTERN = re.compile(
    rf"(?i)\b\d{{4,8}}\b(?=[^\n]{{0,24}}(?:{_SENSITIVE_LABEL}))"
)
_VALUE_BEFORE_LABEL_PATTERN = re.compile(
    rf"(?i)\b[A-Za-z0-9_\-]{{4,}}\b"
    rf"(?=\s+(?:is|が|は)\s+(?:your\s+)?(?:{_SENSITIVE_LABEL}))"
)


def redact_sensitive_values(value: str) -> str:
    """Remove likely authentication values while retaining surrounding context."""
    redacted = _SENSITIVE_VALUE_PATTERN.sub(
        rf"\1\2{REDACTION_MARKER}", value
    )
    redacted = _NUMERIC_CODE_BEFORE_LABEL_PATTERN.sub(REDACTION_MARKER, redacted)
    return _VALUE_BEFORE_LABEL_PATTERN.sub(REDACTION_MARKER, redacted)
