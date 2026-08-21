from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from proactive_mcp import situations
from proactive_mcp.sources.calendar import (
    AllDayDate,
    CalendarEvent,
    ResponseStatus,
    TimedInstant,
)


class FakeClock:
    """A manually advanced clock for deterministic situation tests."""

    _now: datetime

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def require_m3(*names: str) -> None:
    """Fail in test execution when an expected M3 API is not implemented."""
    missing = tuple(name for name in names if not hasattr(situations, name))
    assert not missing, f"missing M3 situation API: {', '.join(missing)}"


def timed_event(
    event_id: str,
    start: datetime,
    end: datetime,
    *,
    response: ResponseStatus | None = "accepted",
    organizer: bool = False,
) -> CalendarEvent:
    return CalendarEvent(
        id=event_id,
        status="confirmed",
        summary=f"Event {event_id}",
        start=TimedInstant(start),
        end=TimedInstant(end),
        is_organizer=organizer,
        self_response_status=response,
    )


def all_day_event(event_id: str, day: date) -> CalendarEvent:
    return CalendarEvent(
        id=event_id,
        status="confirmed",
        summary=f"Event {event_id}",
        start=AllDayDate(day),
        end=AllDayDate(day + timedelta(days=1)),
        is_organizer=True,
        self_response_status=None,
    )


def utc_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)
