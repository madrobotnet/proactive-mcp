"""Typed source snapshots consumed by the situation detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from proactive_mcp.sources.calendar import CalendarEvent

__all__ = ["EngineInputs", "InboxThreadSnapshot"]


@dataclass(frozen=True, slots=True)
class InboxThreadSnapshot:
    """One inbox thread reduced to the fields the reply detector needs.

    ``subject``, ``sender_display``, and ``snippet`` quote external mail
    content; detectors must only surface them inside evidence.
    """

    thread_id: str
    latest_message_id: str
    latest_from_user: bool
    user_is_recipient: bool
    latest_message_at: datetime
    subject: str | None = None
    sender_display: str | None = None
    snippet: str | None = None


@dataclass(frozen=True, slots=True)
class EngineInputs:
    """Fresh source snapshots for one evaluation pass.

    ``None`` means the source could not be read this pass; the engine then
    neither detects nor resolves situations for it and reports a warning
    instead of an all-clear.
    """

    gmail_threads: tuple[InboxThreadSnapshot, ...] | None = None
    calendar_events: tuple[CalendarEvent, ...] | None = None
