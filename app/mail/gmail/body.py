"""Safely extract plain text from Gmail message MIME payloads."""

from __future__ import annotations

import base64
import binascii
import unicodedata
from email.message import Message
from html.parser import HTMLParser
from typing import Any


_HTML_BLOCKED_CONTAINERS = {"head", "noscript", "script", "style"}
_HTML_IGNORED_ELEMENTS = {"meta"}
_HTML_BLOCK_ELEMENTS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocked_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        tag = tag.lower()
        if tag in _HTML_BLOCKED_CONTAINERS:
            self._blocked_depth += 1
            return
        if self._blocked_depth or tag in _HTML_IGNORED_ELEMENTS:
            return
        if tag in _HTML_BLOCK_ELEMENTS:
            self._chunks.append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._blocked_depth:
            if tag in _HTML_BLOCKED_CONTAINERS:
                self._blocked_depth -= 1
            return
        if tag in _HTML_BLOCK_ELEMENTS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self._chunks.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._chunks).splitlines())
        return "\n".join(line for line in lines if line)


def _header_value(part: dict[str, Any], name: str) -> str:
    for header in part.get("headers", []):
        if str(header.get("name", "")).lower() == name.lower():
            return str(header.get("value", ""))
    return ""


def _decode_body(part: dict[str, Any]) -> str:
    encoded_data = part.get("body", {}).get("data")
    if not encoded_data:
        return ""

    try:
        encoded_bytes = str(encoded_data).encode("ascii")
        padding = b"=" * (-len(encoded_bytes) % 4)
        decoded_bytes = base64.urlsafe_b64decode(encoded_bytes + padding)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return ""

    content_type = Message()
    content_type["content-type"] = _header_value(part, "Content-Type")
    charset = content_type.get_content_charset() or "utf-8"
    try:
        return decoded_bytes.decode(charset, errors="replace")
    except LookupError:
        return decoded_bytes.decode("utf-8", errors="replace")


def _is_attachment(part: dict[str, Any]) -> bool:
    if str(part.get("filename", "")).strip():
        return True
    if part.get("body", {}).get("attachmentId"):
        return True
    disposition = _header_value(part, "Content-Disposition").lower()
    return disposition.split(";", 1)[0].strip() == "attachment"


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"} or unicodedata.category(character) != "Cc"
    ).strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # HTMLParser is tolerant, but keep any text recovered before malformed input.
        pass
    return _clean_text(parser.text())


def _collect_body_parts(
    part: dict[str, Any],
    plain_parts: list[str],
    html_parts: list[str],
) -> None:
    if _is_attachment(part):
        return

    mime_type = str(part.get("mimeType", "")).lower()
    if mime_type == "text/plain":
        text = _clean_text(_decode_body(part))
        if text:
            plain_parts.append(text)
        return
    if mime_type == "text/html":
        text = _html_to_text(_decode_body(part))
        if text:
            html_parts.append(text)
        return

    if mime_type.startswith("multipart/"):
        for child in part.get("parts", []):
            _collect_body_parts(child, plain_parts, html_parts)


def extract_message_body(payload: dict[str, Any]) -> str:
    """Extract text/plain, or text converted from HTML, from a MIME payload."""
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _collect_body_parts(payload, plain_parts, html_parts)

    if plain_parts:
        return "\n\n".join(plain_parts)
    if html_parts:
        return "\n\n".join(html_parts)
    return ""


def get_full_message(service: Any, message_id: str) -> dict[str, Any]:
    """Fetch a Gmail message in full format without fetching attachment bodies."""
    return (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",
            fields="id,threadId,internalDate,payload",
        )
        .execute()
    )


def get_message_body(service: Any, message_id: str) -> str:
    """Fetch a Gmail message and return its safely extracted plain-text body."""
    message = get_full_message(service, message_id)
    return extract_message_body(message.get("payload", {}))
