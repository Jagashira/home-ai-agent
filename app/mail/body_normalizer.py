"""Deterministic email body normalization shared by mail providers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_ZERO_WIDTH_CHARACTERS = str.maketrans(
    "",
    "",
    "\u180e\u200b\u200c\u200d\u200e\u200f\u2060\ufeff",
)
_URL_EXPRESSION = (
    r"(?:https?://|www\.)[^\s<>()]+"
    r"|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}/[^\s<>()]*"
)
_URL_PATTERN = re.compile(
    rf"\(\s*(?P<parenthesized>{_URL_EXPRESSION})\s*\)|(?P<bare>{_URL_EXPRESSION})",
    re.IGNORECASE,
)
_TRACKING_QUERY_KEYS = {
    "campaign_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "vero_conv",
    "vero_id",
}
_GENERIC_IMAGE_ALT = re.compile(
    r"^(?:|\.{3}|image|null|none|spacer|tracking(?: pixel)?|pixel|"
    r"\d+\s*[x×]\s*\d+|[^\s]+\.(?:gif|jpe?g|png|webp))$",
    re.IGNORECASE,
)
_IMAGE_MARKER = re.compile(r"\[image:\s*([^\]]*)\]", re.IGNORECASE)
_JAPANESE_CHARACTER = r"ぁ-んァ-ヶ一-龠々〆ヵヶ"


def _clean_url(url: str) -> str | None:
    prefixed = not url.lower().startswith(("http://", "https://"))
    parseable_url = f"https://{url}" if prefixed else url
    parsed = urlsplit(parseable_url)
    hostname = (parsed.hostname or "").lower()
    first_host_label = hostname.split(".", 1)[0]
    path = parsed.path.lower()

    tracking_host = first_host_label in {"click", "clicks", "track", "tracking", "trk"}
    tracking_path = bool(re.search(r"/(?:click|redirect|track)(?:/|$)", path))
    if tracking_host and (tracking_path or parsed.query or len(url) > 120):
        return None

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    filtered_query = [
        (key, value)
        for key, value in query_items
        if not key.lower().startswith("utm_")
        and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    cleaned = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(filtered_query, doseq=True),
            parsed.fragment,
        )
    )
    if prefixed:
        cleaned = cleaned.removeprefix("https://")
    return cleaned


def _normalize_urls(text: str) -> str:
    seen_urls: set[str] = set()

    def replace_url(match: re.Match[str]) -> str:
        url = match.group("parenthesized") or match.group("bare")
        trailing_punctuation = ""
        while url and url[-1] in ".,;:!?":
            trailing_punctuation = url[-1] + trailing_punctuation
            url = url[:-1]

        cleaned_url = _clean_url(url)
        if cleaned_url is None:
            return trailing_punctuation

        identity = cleaned_url.lower().rstrip("/")
        if identity in seen_urls:
            return trailing_punctuation
        seen_urls.add(identity)

        if match.group("parenthesized") is not None:
            return f"({cleaned_url}){trailing_punctuation}"
        return f"{cleaned_url}{trailing_punctuation}"

    return _URL_PATTERN.sub(replace_url, text)


def _remove_image_noise(text: str) -> str:
    def replace_image_marker(match: re.Match[str]) -> str:
        alt_text = " ".join(match.group(1).split())
        if _GENERIC_IMAGE_ALT.fullmatch(alt_text):
            return ""
        return f"[image: {alt_text}]"

    return _IMAGE_MARKER.sub(replace_image_marker, text)


def _join_obvious_line_wraps(text: str) -> str:
    text = re.sub(r"(?<=[A-Za-z]{3})-\n(?=[a-z]{2})", "", text)
    text = re.sub(
        rf"(?<=[{_JAPANESE_CHARACTER}、])\n(?=[{_JAPANESE_CHARACTER}])",
        "",
        text,
    )

    lines = text.split("\n")
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        while (
            len(line) >= 50
            and index + 1 < len(lines)
            and lines[index + 1]
            and not re.search(r"[.!?。！？:]$", line)
            and not re.match(r"^(?:[-*•]|\d+[.)])\s", lines[index + 1])
        ):
            index += 1
            line = f"{line} {lines[index].lstrip()}"
        joined.append(line)
        index += 1
    return "\n".join(joined)


def _remove_consecutive_duplicates(text: str) -> str:
    result: list[str] = []
    previous_nonempty: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and stripped == previous_nonempty and len(stripped) <= 160:
            continue
        result.append(line)
        previous_nonempty = stripped or None
    return "\n".join(result)


def normalize_email_body(text: str) -> str:
    """Return a deterministic, meaning-preserving normalization of email text."""
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ").replace("\u2007", " ")
    normalized = normalized.replace("\u202f", " ").translate(_ZERO_WIDTH_CHARACTERS)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _normalize_urls(normalized)
    normalized = _remove_image_noise(normalized)
    normalized = re.sub(r"(?im)^\s*null\s*$", "", normalized)
    normalized = _join_obvious_line_wraps(normalized)
    normalized = _remove_consecutive_duplicates(normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
