"""Deterministic local-date helpers shared by the situation detectors."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from datetime import tzinfo

_FULL_ANCHOR: Final[re.Pattern[str]] = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEARLESS_ANCHOR: Final[re.Pattern[str]] = re.compile(r"^--(\d{2})-(\d{2})$")
_FEBRUARY: Final = 2
_LEAP_DAY: Final = 29


def parse_anchor(anchor: str) -> tuple[int | None, int, int] | None:
    """Parse a date anchor into (year, month, day); year is None for --MM-DD."""
    full = _FULL_ANCHOR.match(anchor)
    if full is not None:
        year, month, day = (int(group) for group in full.groups())
        return (year, month, day) if _is_valid_date(year, month, day) else None
    yearless = _YEARLESS_ANCHOR.match(anchor)
    if yearless is None:
        return None
    month, day = (int(group) for group in yearless.groups())
    leap_reference_year = 2024
    if not _is_valid_date(leap_reference_year, month, day):
        return None
    return (None, month, day)


def yearly_occurrence_on_or_after(today: date, month: int, day: int) -> date:
    """Return the next yearly occurrence of month/day on or after today.

    February 29 anchors fall back to February 28 in non-leap years.
    """
    occurrence = _occurrence_in_year(today.year, month, day)
    if occurrence >= today:
        return occurrence
    return _occurrence_in_year(today.year + 1, month, day)


def local_day_start(day: date, tz: tzinfo) -> datetime:
    """Return the UTC instant when the given local calendar day starts."""
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def local_day_end(day: date, tz: tzinfo) -> datetime:
    """Return the UTC instant when the given local calendar day ends."""
    return local_day_start(day + timedelta(days=1), tz)


def _occurrence_in_year(year: int, month: int, day: int) -> date:
    if month == _FEBRUARY and day == _LEAP_DAY and not _is_leap_year(year):
        return date(year, _FEBRUARY, _LEAP_DAY - 1)
    return date(year, month, day)


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _is_valid_date(year: int, month: int, day: int) -> bool:
    try:
        _ = date(year, month, day)
    except ValueError:
        return False
    return True
