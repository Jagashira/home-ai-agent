"""Conservative deterministic normalization for same-case mail matching."""

from __future__ import annotations

import re
import unicodedata


_DECORATIVE_BRACKETS = str.maketrans("", "", "【】[]［］「」『』〈〉《》")
_DECORATIVE_EDGE_CHARACTERS = "★☆●○■□◆◇▲△▼▽※♪♬|｜"


def normalize_case_text(value: str, *, subject: bool = False) -> str:
    """Normalize exact case keys without fuzzy or semantic matching."""
    normalized = unicodedata.normalize("NFKC", value)
    if subject:
        normalized = normalized.translate(_DECORATIVE_BRACKETS).strip()
        normalized = normalized.strip(_DECORATIVE_EDGE_CHARACTERS).strip()
    return re.sub(r"\s+", " ", normalized).strip()
