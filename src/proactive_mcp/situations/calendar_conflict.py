"""Deterministic calendar-conflict detection over calendar event snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

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
    "detect_calendar_conflicts",
]

DEFAULT_CRITICAL_WINDOW: Final = timedelta(hours=2)
DEFAULT_HIGH_WINDOW: Final = timedelta(hours=24)
_SECONDS_PER_HOUR: Final = 3600


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
    """Detect overlapping committed events, per product plan §6.2.

    Only confirmed events the user organizes or accepted take part, and
    only while they mark the user busy (``opaque``); free (``transparent``)
    events never conflict. Pairs of all-day events are excluded. A conflict
    resolves naturally once one event moves or is cancelled, because its
    pair key is then no longer detected.
    """
    intervals = _committed_intervals(events, tz)
    detections: list[Detection] = []
    for index, first in enumerate(intervals):
        for second in intervals[index + 1 :]:
            if first.all_day and second.all_day:
                continue
            overlap_start = max(first.start, second.start)
            overlap_end = min(first.end, second.end)
            if overlap_start >= overlap_end or overlap_end <= now:
                continue
            detections.append(
                _detection(
                    (first, second),
                    overlap=(overlap_start, overlap_end),
                    now=now,
                    tz=tz,
                    windows=(critical_window, high_window),
                )
            )
    return tuple(detections)


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
    first_start = min(first.start, second.start)
    priority = _priority(first_start, now, critical_window, high_window)
    local_start = overlap_start.astimezone(tz)
    local_end = overlap_end.astimezone(tz)
    lead_seconds = (first_start - now).total_seconds()
    urgency = (
        "the earlier event already started"
        if lead_seconds <= 0
        else f"the earlier event starts in {int(lead_seconds // _SECONDS_PER_HOUR)}h"
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
    first_start: datetime,
    now: datetime,
    critical_window: timedelta,
    high_window: timedelta,
) -> SituationPriority:
    if first_start <= now + critical_window:
        return "critical"
    if first_start <= now + high_window:
        return "high"
    return "routine"
