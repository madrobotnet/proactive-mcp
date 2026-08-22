"""Typed Calendar values shared by source adapters and situation detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from datetime import date, datetime

EventStatus: TypeAlias = Literal["confirmed", "tentative", "cancelled"]
EventTransparency: TypeAlias = Literal["opaque", "transparent"]
ResponseStatus: TypeAlias = Literal["needsAction", "declined", "tentative", "accepted"]


@dataclass(frozen=True, slots=True)
class TimedInstant:
    """A timezone-aware event bound expressed as a UTC instant."""

    instant: datetime
    is_all_day: bool = False


@dataclass(frozen=True, slots=True)
class AllDayDate:
    """An all-day event bound expressed as a calendar date."""

    all_day_date: date
    is_all_day: bool = True


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """A typed Calendar event with the flags later detectors need."""

    id: str
    status: EventStatus
    summary: str | None
    start: TimedInstant | AllDayDate | None
    end: TimedInstant | AllDayDate | None
    is_organizer: bool
    self_response_status: ResponseStatus | None
    transparency: EventTransparency = "opaque"


@dataclass(frozen=True, slots=True)
class CalendarReadResult:
    """In-memory result of a primary-calendar events read."""

    events: tuple[CalendarEvent, ...]
    fetched_at: str
    window_start: str
    window_end: str
    page_count: int
    skipped_count: int
