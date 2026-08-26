from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from proactive_mcp import situations
from proactive_mcp.store import NewMemory, Store
from tests.situation_test_support import (
    FakeClock,
    require_m3,
    timed_event,
    utc_datetime,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.sources.calendar import CalendarEvent

_NO_THREAD_IDS: Final[frozenset[str]] = frozenset()


def _open_engine(
    tmp_path: Path,
) -> tuple[Store, situations.SituationEngine, FakeClock]:
    require_m3("EngineInputs", "SituationEngine")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    store = Store(tmp_path / "situations.db", clock=clock)
    return store, situations.SituationEngine(store, clock, UTC), clock


def _gmail_snapshot(  # noqa: PLR0913
    store: Store,
    threads: tuple[situations.InboxThreadSnapshot, ...],
    *,
    complete: bool = True,
    warning_codes: tuple[str, ...] = (),
    resolve_absent: bool = False,
    resolution_excluded_ids: frozenset[str] = _NO_THREAD_IDS,
) -> situations.SourceSnapshot[situations.InboxThreadSnapshot]:
    require_m3("SourceSnapshot")
    return situations.SourceSnapshot(
        generation=store.reserve_source_generation("gmail"),
        items=threads,
        complete=complete,
        warning_codes=warning_codes,
        resolve_absent=resolve_absent,
        resolution_excluded_ids=resolution_excluded_ids,
    )


def _calendar_snapshot(
    store: Store,
    events: tuple[CalendarEvent, ...],
    *,
    complete: bool = True,
    warning_codes: tuple[str, ...] = (),
) -> situations.SourceSnapshot[CalendarEvent]:
    require_m3("SourceSnapshot")
    return situations.SourceSnapshot(
        generation=store.reserve_source_generation("calendar"),
        items=events,
        complete=complete,
        warning_codes=warning_codes,
    )


def _conflicting_events(now: datetime) -> tuple[CalendarEvent, CalendarEvent]:
    return (
        timed_event("event-a", now + timedelta(hours=3), now + timedelta(hours=4)),
        timed_event(
            "event-b",
            now + timedelta(hours=3, minutes=30),
            now + timedelta(hours=5),
        ),
    )


def test_degraded_gmail_reconciles_unrelated_safe_absence(tmp_path: Path) -> None:
    store, engine, clock = _open_engine(tmp_path)
    try:
        threads = tuple(
            situations.InboxThreadSnapshot(
                thread_id=f"thread-{suffix}",
                latest_message_id=f"message-{suffix}",
                latest_from_user=False,
                user_is_recipient=True,
                latest_message_at=clock.now() - timedelta(hours=49),
            )
            for suffix in ("safe", "degraded")
        )
        initial = engine.evaluate(
            situations.EngineInputs(gmail_threads=_gmail_snapshot(store, threads))
        )
        assert initial.created == 2

        degraded = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(
                    store,
                    (),
                    complete=False,
                    warning_codes=("gmail_body_incomplete",),
                    resolve_absent=True,
                    resolution_excluded_ids=frozenset({"thread-degraded"}),
                )
            )
        )

        states = {
            item.evidence.facts["thread_id"]: item.state
            for item in store.situations.list_situations()
        }
    finally:
        store.close()

    assert degraded.resolved == 1
    assert degraded.gmail_freshness.status == "error"
    assert states == {"thread-safe": "resolved", "thread-degraded": "pending"}


def test_calendar_overflow_is_degraded_and_preserves_existing_truth(
    tmp_path: Path,
) -> None:
    # Given: one persisted conflict from a complete Calendar generation.
    store, engine, clock = _open_engine(tmp_path)
    try:
        initial = engine.evaluate(
            situations.EngineInputs(
                calendar_events=_calendar_snapshot(
                    store,
                    _conflicting_events(clock.now()),
                )
            )
        )
        assert initial.created == 1
        existing = store.situations.list_situations()[0]
        dense = tuple(
            timed_event(
                f"dense-{index}",
                clock.now() + timedelta(hours=1),
                clock.now() + timedelta(hours=3),
            )
            for index in range(46)
        )

        # When: the next snapshot exceeds the bounded conflict output.
        result = engine.evaluate(
            situations.EngineInputs(calendar_events=_calendar_snapshot(store, dense))
        )

        # Then: positive findings persist but omitted absence cannot resolve old truth.
        assert "calendar: calendar_conflict_output_overflow" in result.warnings
        assert store.source_generation_state("calendar").status == "degraded"
        persisted = store.situations.get_situation(existing.id)
        assert persisted is not None
        assert persisted.state == "pending"
    finally:
        store.close()


def test_degraded_gmail_generation_preserves_rows_and_independent_sources(
    tmp_path: Path,
) -> None:
    # Given: complete Gmail, Calendar, and local detections are persisted together.
    store, engine, clock = _open_engine(tmp_path)
    try:
        clock.set(utc_datetime(2026, 7, 11, 9))
        _ = store.remember(
            NewMemory(
                kind="fact",
                entity="Mother",
                entity_kind="person",
                attribute="birthday",
                content="Fixture birthday",
                date_anchor="--07-18",
                recurrence="yearly",
                lead_days=7,
            )
        )
        thread = situations.InboxThreadSnapshot(
            thread_id="generation-thread",
            latest_message_id="generation-message",
            latest_from_user=False,
            user_is_recipient=True,
            latest_message_at=clock.now() - timedelta(hours=49),
        )
        initial = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, (thread,)),
                calendar_events=_calendar_snapshot(
                    store,
                    _conflicting_events(clock.now()),
                ),
            )
        )
        assert initial.created == 3

        # When: a newer Gmail generation is degraded while Calendar stays complete.
        degraded = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, (), complete=False),
                calendar_events=_calendar_snapshot(
                    store,
                    _conflicting_events(clock.now()),
                ),
            )
        )
        stored = store.situations.list_situations(limit=10)

        # Then: source generations persist independently and no Gmail row is deleted.
        gmail_state = store.source_generation_state("gmail")
        calendar_state = store.source_generation_state("calendar")
        assert degraded.resolved == 0
        assert (gmail_state.issued, gmail_state.applied, gmail_state.status) == (
            2,
            2,
            "degraded",
        )
        calendar_generation = (
            calendar_state.issued,
            calendar_state.applied,
            calendar_state.status,
        )
        assert calendar_generation == (2, 2, "complete")
        assert {item.situation_type for item in stored} == {
            "reply_deadline",
            "calendar_conflict",
            "personal_occasion",
        }
        assert all(item.state == "pending" for item in stored)
    finally:
        store.close()
