from __future__ import annotations

from datetime import UTC, timedelta

import pytest

from proactive_mcp import situations
from proactive_mcp.sources.calendar import CalendarEvent
from tests.situation_test_support import (
    all_day_event,
    require_m3,
    timed_event,
    utc_datetime,
)


def test_calendar_conflict_detects_overlap_with_order_independent_key() -> None:
    # Given: accepted and owner events that overlap within two hours.
    require_m3("detect_calendar_conflicts")
    now = utc_datetime(2026, 8, 21, 12)
    first = timed_event(
        "z-event",
        now + timedelta(hours=1),
        now + timedelta(hours=2),
        organizer=True,
        response=None,
    )
    second = timed_event(
        "a-event",
        now + timedelta(hours=1, minutes=30),
        now + timedelta(hours=3),
    )

    # When: source order is reversed across equivalent evaluations.
    forward = situations.detect_calendar_conflicts(
        events=(first, second),
        now=now,
        tz=UTC,
    )
    reverse = situations.detect_calendar_conflicts(
        events=(second, first),
        now=now,
        tz=UTC,
    )

    # Then: one critical conflict has the same sorted-pair identity.
    assert len(forward) == len(reverse) == 1
    assert forward[0].situation_type == "calendar_conflict"
    assert forward[0].priority == "critical"
    assert forward[0].dedupe_key == reverse[0].dedupe_key
    assert forward[0].dedupe_key == "calendar_conflict:a-event:z-event"
    assert {
        forward[0].evidence.facts["event_a_id"],
        forward[0].evidence.facts["event_b_id"],
    } == {"a-event", "z-event"}


def test_calendar_conflict_excludes_non_conflicting_event_classes() -> None:
    # Given: touching, all-day, declined, cancelled, and transparent events.
    require_m3("detect_calendar_conflicts")
    now = utc_datetime(2026, 8, 21, 12)
    touching = (
        timed_event("touch-a", now + timedelta(hours=3), now + timedelta(hours=4)),
        timed_event("touch-b", now + timedelta(hours=4), now + timedelta(hours=5)),
    )
    declined = (
        timed_event("accepted", now + timedelta(hours=6), now + timedelta(hours=8)),
        timed_event(
            "declined",
            now + timedelta(hours=7),
            now + timedelta(hours=9),
            response="declined",
        ),
    )
    cancelled = CalendarEvent(
        id="cancelled",
        status="cancelled",
        summary="Cancelled event",
        start=touching[0].start,
        end=touching[0].end,
        is_organizer=True,
        self_response_status=None,
    )
    transparent = CalendarEvent(
        id="free",
        status="confirmed",
        summary="Free event",
        start=touching[0].start,
        end=touching[0].end,
        is_organizer=True,
        self_response_status=None,
        transparency="transparent",
    )

    # When: each excluded class is evaluated beside another event.
    results = (
        situations.detect_calendar_conflicts(events=touching, now=now, tz=UTC),
        situations.detect_calendar_conflicts(events=declined, now=now, tz=UTC),
        situations.detect_calendar_conflicts(
            events=(
                all_day_event("day-a", now.date()),
                all_day_event("day-b", now.date()),
            ),
            now=now,
            tz=UTC,
        ),
        situations.detect_calendar_conflicts(
            events=(touching[0], cancelled, transparent),
            now=now,
            tz=UTC,
        ),
    )

    # Then: no false conflict is produced.
    assert results == ((), (), (), ())


@pytest.mark.parametrize(
    ("start_offset", "expected_priority"),
    [
        (timedelta(hours=2), "critical"),
        (timedelta(hours=2, seconds=1), "high"),
        (timedelta(hours=24, seconds=1), "routine"),
    ],
)
def test_calendar_conflict_priority_obeys_time_window_boundaries(
    start_offset: timedelta,
    expected_priority: str,
) -> None:
    # Given: one isolated conflict pair at a priority boundary.
    require_m3("detect_calendar_conflicts")
    now = utc_datetime(2026, 8, 21, 12)
    start = now + start_offset
    events = (
        timed_event("boundary-a", start, start + timedelta(hours=1)),
        timed_event(
            "boundary-b",
            start + timedelta(minutes=15),
            start + timedelta(hours=2),
        ),
    )

    # When: the pair is evaluated without unrelated cross-pair overlaps.
    detected = situations.detect_calendar_conflicts(events=events, now=now, tz=UTC)

    # Then: start proximity assigns the expected boundary priority.
    assert len(detected) == 1
    assert detected[0].priority == expected_priority
