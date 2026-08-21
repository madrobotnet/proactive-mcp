"""Deterministic calendar-conflict detection over calendar event snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from heapq import heappop, heappush
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from proactive_mcp.sources.calendar import AllDayDate, TimedInstant
from proactive_mcp.store import Detection, SituationEvidence

from ._dates import local_day_start

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, tzinfo

    from proactive_mcp.sources.calendar import CalendarEvent
    from proactive_mcp.store import SituationPriority

__all__ = [
    "DEFAULT_CRITICAL_WINDOW",
    "DEFAULT_HIGH_WINDOW",
    "MAX_CALENDAR_CONFLICTS",
    "CalendarConflictRun",
    "CalendarConflictWarningCode",
    "detect_calendar_conflicts",
    "run_calendar_conflict_detection",
]

DEFAULT_CRITICAL_WINDOW: Final = timedelta(hours=2)
DEFAULT_HIGH_WINDOW: Final = timedelta(hours=24)
MAX_CALENDAR_CONFLICTS: Final = 1_000
_SECONDS_PER_HOUR: Final = 3600
CalendarConflictWarningCode: TypeAlias = Literal["calendar_conflict_output_overflow"]


@dataclass(frozen=True, slots=True)
class CalendarConflictRun:
    """Bounded conflict output and whether absence is safe for resolution."""

    detections: tuple[Detection, ...]
    resolution_safe: bool
    warning_codes: tuple[CalendarConflictWarningCode, ...]


@dataclass(frozen=True, slots=True)
class _Interval:
    """One committed event reduced to a UTC interval."""

    event: CalendarEvent
    start: datetime
    end: datetime
    all_day: bool


def detect_calendar_conflicts(
    events: Sequence[CalendarEvent],
    *,
    now: datetime,
    tz: tzinfo,
    critical_window: timedelta = DEFAULT_CRITICAL_WINDOW,
    high_window: timedelta = DEFAULT_HIGH_WINDOW,
) -> tuple[Detection, ...]:
    """Return the compatible tuple view of a bounded conflict run."""
    return run_calendar_conflict_detection(
        events,
        now=now,
        tz=tz,
        critical_window=critical_window,
        high_window=high_window,
    ).detections


def run_calendar_conflict_detection(
    events: Sequence[CalendarEvent],
    *,
    now: datetime,
    tz: tzinfo,
    critical_window: timedelta = DEFAULT_CRITICAL_WINDOW,
    high_window: timedelta = DEFAULT_HIGH_WINDOW,
) -> CalendarConflictRun:
    """Detect conflicts with deterministic ordering and bounded output.

    Only confirmed, busy events the user organizes or accepted participate.
    All-day pairs are excluded. If output overflows, ``resolution_safe`` is
    false because an omitted pair must not be interpreted as resolved.
    """
    intervals = sorted(
        (
            interval
            for interval in _committed_intervals(events, tz)
            if interval.end > now
        ),
        key=lambda interval: (interval.start, interval.end, interval.event.id),
    )
    detections: list[Detection] = []
    active: dict[int, _Interval] = {}
    active_timed: dict[int, _Interval] = {}
    ends: list[tuple[datetime, int]] = []
    for sequence, current in enumerate(intervals):
        while ends and ends[0][0] <= current.start:
            _, expired_sequence = heappop(ends)
            expired = active.pop(expired_sequence)
            if not expired.all_day:
                _ = active_timed.pop(expired_sequence)
        candidates = active_timed.values() if current.all_day else active.values()
        for previous in candidates:
            overlap_start = current.start
            overlap_end = min(previous.end, current.end)
            if overlap_end <= now:
                continue
            if len(detections) == MAX_CALENDAR_CONFLICTS:
                return CalendarConflictRun(
                    detections=tuple(detections),
                    resolution_safe=False,
                    warning_codes=("calendar_conflict_output_overflow",),
                )
            detections.append(
                _detection(
                    (previous, current),
                    overlap=(overlap_start, overlap_end),
                    now=now,
                    tz=tz,
                    windows=(critical_window, high_window),
                )
            )
        active[sequence] = current
        if not current.all_day:
            active_timed[sequence] = current
        heappush(ends, (current.end, sequence))
    return CalendarConflictRun(
        detections=tuple(detections),
        resolution_safe=True,
        warning_codes=(),
    )


def _committed_intervals(
    events: Sequence[CalendarEvent],
    tz: tzinfo,
) -> tuple[_Interval, ...]:
    intervals: list[_Interval] = []
    for event in events:
        if event.status != "confirmed" or event.transparency != "opaque":
            continue
        if not (event.is_organizer or event.self_response_status == "accepted"):
            continue
        start = _instant(event.start, tz)
        end = _instant(event.end, tz)
        if start is None or end is None:
            continue
        all_day = isinstance(event.start, AllDayDate)
        intervals.append(_Interval(event=event, start=start, end=end, all_day=all_day))
    return tuple(intervals)


def _instant(
    bound: TimedInstant | AllDayDate | None,
    tz: tzinfo,
) -> datetime | None:
    if isinstance(bound, TimedInstant):
        return bound.instant
    if isinstance(bound, AllDayDate):
        return local_day_start(bound.all_day_date, tz)
    return None


def _detection(
    pair: tuple[_Interval, _Interval],
    *,
    overlap: tuple[datetime, datetime],
    now: datetime,
    tz: tzinfo,
    windows: tuple[timedelta, timedelta],
) -> Detection:
    first, second = pair
    overlap_start, overlap_end = overlap
    critical_window, high_window = windows
    priority = _priority(overlap_start, now, critical_window, high_window)
    local_start = overlap_start.astimezone(tz)
    local_end = overlap_end.astimezone(tz)
    lead_seconds = (overlap_start - now).total_seconds()
    urgency = (
        "the overlap already started"
        if lead_seconds <= 0
        else f"the overlap starts in {int(lead_seconds // _SECONDS_PER_HOUR)}h"
    )
    low_id, high_id = sorted((first.event.id, second.event.id))
    quoted: dict[str, str] = {}
    for label, interval in (("event_a", first), ("event_b", second)):
        if interval.event.summary is not None:
            quoted[label] = interval.event.summary
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=f"calendar_conflict:{low_id}:{high_id}",
        priority=priority,
        title=f"Calendar conflict on {local_start.date().isoformat()}",
        why_now=(
            f"Two committed events overlap"
            f" {local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}"
            f" on {local_start.date().isoformat()}; {urgency}"
        ),
        evidence=SituationEvidence(
            facts={
                "event_a_id": first.event.id,
                "event_b_id": second.event.id,
                "event_a_start": first.start.isoformat(),
                "event_a_end": first.end.isoformat(),
                "event_b_start": second.start.isoformat(),
                "event_b_end": second.end.isoformat(),
                "overlap_start": overlap_start.isoformat(),
                "overlap_end": overlap_end.isoformat(),
            },
            quoted_external=quoted,
        ),
        expires_at=overlap_end,
    )


def _priority(
    overlap_start: datetime,
    now: datetime,
    critical_window: timedelta,
    high_window: timedelta,
) -> SituationPriority:
    if overlap_start <= now + critical_window:
        return "critical"
    if overlap_start <= now + high_window:
        return "high"
    return "routine"
