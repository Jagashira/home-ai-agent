"""Minimal add-only Gmail label operations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def list_user_label_ids(service: Any) -> dict[str, str]:
    """Return existing user label names mapped to Gmail label IDs."""
    response = service.users().labels().list(userId="me").execute()
    return {
        str(label["name"]): str(label["id"])
        for label in response.get("labels", [])
        if label.get("type") == "user" and label.get("name") and label.get("id")
    }


def create_user_label(service: Any, name: str) -> str:
    """Create one visible Gmail user label and return its ID."""
    response = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return str(response["id"])


def get_message_label_ids(service: Any, message_id: str) -> set[str]:
    """Read the labels currently attached to one Gmail message."""
    response = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="minimal",
            fields="id,labelIds",
        )
        .execute()
    )
    return {str(label_id) for label_id in response.get("labelIds", [])}


def add_labels_to_message(
    service: Any,
    message_id: str,
    label_ids: Iterable[str],
) -> None:
    """Add labels without removing labels or changing any other message state."""
    additions = sorted(set(label_ids))
    if not additions:
        return
    (
        service.users()
        .messages()
        .modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": additions},
        )
        .execute()
    )
