"""Conservative Korean and English deadline-language scanning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = ["DeadlineScan", "scan_deadline_text"]

_MAX_SCAN_LENGTH: Final = 4000
_ROLLOVER_GRACE: Final = timedelta(days=7)

_MARKERS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bdeadline\b", re.IGNORECASE),
    re.compile(r"\bdue\b", re.IGNORECASE),
    re.compile(r"\basap\b", re.IGNORECASE),
    re.compile(r"\beod\b", re.IGNORECASE),
    re.compile(r"\bcob\b", re.IGNORECASE),
    re.compile(r"\bno later than\b", re.IGNORECASE),
    re.compile(r"\b(?:respond|reply|rsvp)\s+by\b", re.IGNORECASE),
    re.compile(r"\bby\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day\b", re.IGNORECASE),
    re.compile(r"\bby\s+(?:today|tomorrow)\b", re.IGNORECASE),
    re.compile(r"\bby\s+end of (?:day|week|month)\b", re.IGNORECASE),
    re.compile(r"마감"),
    re.compile(r"기한"),
    re.compile(r"회신\s*(?:요망|부탁)"),
    re.compile(r"답장\s*부탁"),
    re.compile(r"늦어도"),
    re.compile(r"(?:\d|[일늘레])\s*까지"),
)

_ISO_DATE: Final[re.Pattern[str]] = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_KOREAN_DATE: Final[re.Pattern[str]] = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_ENGLISH_DATE: Final[re.Pattern[str]] = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_SLASH_DATE: Final[re.Pattern[str]] = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_RELATIVE: Final[tuple[tuple[re.Pattern[str], int], ...]] = (
    (re.compile(r"오늘\s*까지"), 0),
    (re.compile(r"내일\s*까지"), 1),
    (re.compile(r"모레\s*까지"), 2),
    (re.compile(r"\b(?:by|due)\s+today\b", re.IGNORECASE), 0),
    (re.compile(r"\b(?:by|due)\s+tomorrow\b", re.IGNORECASE), 1),
)
_MONTH_NUMBERS: Final[dict[str, int]] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True, slots=True)
class DeadlineScan:
    """The deterministic outcome of scanning one text for deadline language."""

    matched_marker: str | None
    deadline_date: date | None

    @property
    def has_marker(self) -> bool:
        """Return whether any conservative deadline marker matched."""
        return self.matched_marker is not None


def scan_deadline_text(text: str, *, today: date) -> DeadlineScan:
    """Scan untrusted text for deadline markers and an explicit deadline date.

    The extracted date only refines priority; the marker alone decides
    whether deadline language is present. Year-less dates resolve to the
    current year, rolling to the next year once they are more than seven
    days in the past.
    """
    scanned = text[:_MAX_SCAN_LENGTH]
    marker = _first_marker(scanned)
    if marker is None:
        return DeadlineScan(matched_marker=None, deadline_date=None)
    candidates = _date_candidates(scanned, today)
    return DeadlineScan(
        matched_marker=marker,
        deadline_date=min(candidates) if candidates else None,
    )


def _first_marker(text: str) -> str | None:
    for pattern in _MARKERS:
        matched = pattern.search(text)
        if matched is not None:
            return matched.group(0)
    return None


def _date_candidates(text: str, today: date) -> tuple[date, ...]:
    candidates: list[date] = []
    extractors: tuple[Callable[[str, date], tuple[date, ...]], ...] = (
        _iso_dates,
        _korean_dates,
        _english_dates,
        _slash_dates,
        _relative_dates,
    )
    for extract in extractors:
        candidates.extend(extract(text, today))
    return tuple(candidates)


def _iso_dates(text: str, _today: date) -> tuple[date, ...]:
    found: list[date] = []
    for match in _ISO_DATE.finditer(text):
        parsed = _safe_date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if parsed is not None:
            found.append(parsed)
    return tuple(found)


def _korean_dates(text: str, today: date) -> tuple[date, ...]:
    return _yearless_dates(
        (
            (int(match.group(1)), int(match.group(2)))
            for match in _KOREAN_DATE.finditer(text)
        ),
        today,
    )


def _english_dates(text: str, today: date) -> tuple[date, ...]:
    return _yearless_dates(
        (
            (_MONTH_NUMBERS[match.group(1).lower()], int(match.group(2)))
            for match in _ENGLISH_DATE.finditer(text)
        ),
        today,
    )


def _slash_dates(text: str, today: date) -> tuple[date, ...]:
    return _yearless_dates(
        (
            (int(match.group(1)), int(match.group(2)))
            for match in _SLASH_DATE.finditer(text)
        ),
        today,
    )


def _relative_dates(text: str, today: date) -> tuple[date, ...]:
    found: list[date] = []
    for pattern, offset_days in _RELATIVE:
        if pattern.search(text) is not None:
            found.append(today + timedelta(days=offset_days))
    return tuple(found)


def _yearless_dates(
    month_day_pairs: Iterable[tuple[int, int]],
    today: date,
) -> tuple[date, ...]:
    found: list[date] = []
    for month, day in month_day_pairs:
        candidate = _safe_date(today.year, month, day)
        if candidate is None:
            continue
        if candidate < today - _ROLLOVER_GRACE:
            candidate = _safe_date(today.year + 1, month, day)
            if candidate is None:
                continue
        found.append(candidate)
    return tuple(found)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
