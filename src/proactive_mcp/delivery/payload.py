"""Isolated OS-notification payload: situation type and fixed safe label only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, assert_never

if TYPE_CHECKING:
    from proactive_mcp.store import SituationType

__all__ = [
    "NotificationPayload",
    "NotificationSource",
    "notification_payload",
]


class NotificationSource(Protocol):
    """The only situation fields an OS toast may observe."""

    @property
    def situation_type(self) -> SituationType:
        """Server-assigned situation type."""
        ...


@dataclass(frozen=True, slots=True)
class NotificationPayload:
    """Minimum toast context allowed across the OS-notification boundary."""

    situation_type: str
    title: str


def notification_payload(source: NotificationSource) -> NotificationPayload:
    """Build a PII-free OS notification label from the situation type."""
    situation_type = source.situation_type
    match situation_type:
        case "reply_deadline":
            title = "Reply needed"
        case "calendar_conflict":
            title = "Calendar conflict"
        case "personal_occasion":
            title = "Upcoming personal occasion"
        case _:
            assert_never(situation_type)
    return NotificationPayload(situation_type=situation_type, title=title)
