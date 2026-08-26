from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from proactive_mcp import situations
from proactive_mcp.store import Store
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


def test_resync_dedupes_same_detection_without_resetting_state(tmp_path: Path) -> None:
    # Given: one fresh calendar conflict snapshot.
    store, engine, clock = _open_engine(tmp_path)
    try:
        first_inputs = situations.EngineInputs(
            gmail_threads=_gmail_snapshot(store, ()),
            calendar_events=_calendar_snapshot(
                store,
                _conflicting_events(clock.now()),
            ),
        )

        # When: the exact source snapshot is evaluated twice.
        first = engine.evaluate(first_inputs)
        second = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, ()),
                calendar_events=_calendar_snapshot(
                    store,
                    _conflicting_events(clock.now()),
                ),
            )
        )

        # Then: only one persisted situation exists and the resync refreshes it.
        assert first.created == 1
        assert second.created == 0
        assert second.refreshed == 1
        assert second.resolved == 0
        assert len(store.situations.list_situations()) == 1
    finally:
        store.close()


def test_fresh_source_naturally_resolves_delivered_situation(tmp_path: Path) -> None:
    # Given: a delivered conflict from a previous fresh source snapshot.
    store, engine, clock = _open_engine(tmp_path)
    try:
        created = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, ()),
                calendar_events=_calendar_snapshot(
                    store,
                    _conflicting_events(clock.now()),
                ),
            )
        )
        assert created.created == 1
        situation = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((situation.id,))

        # When: a fresh resync no longer contains the conflict.
        result = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, ()),
                calendar_events=_calendar_snapshot(store, ()),
            )
        )

        # Then: natural source resolution moves delivered to resolved.
        assert result.resolved == 1
        persisted = store.situations.get_situation(situation.id)
        assert persisted is not None
        assert persisted.state == "resolved"
    finally:
        store.close()


def test_stale_source_keeps_missing_items_and_emits_warning(tmp_path: Path) -> None:
    # Given: one pending Gmail situation from a previous fresh evaluation.
    store, engine, clock = _open_engine(tmp_path)
    try:
        thread = situations.InboxThreadSnapshot(
            thread_id="thread",
            latest_message_id="message",
            latest_from_user=False,
            user_is_recipient=True,
            latest_message_at=clock.now() - timedelta(hours=49),
        )
        first = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, (thread,)),
                calendar_events=_calendar_snapshot(store, ()),
            )
        )
        assert first.created == 1
        situation = store.situations.list_situations()[0]
        clock.advance(timedelta(hours=24))

        # When: the next Gmail pass has no snapshot and freshness is stale.
        result = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=None,
                calendar_events=_calendar_snapshot(store, ()),
            )
        )

        # Then: absence is not trusted as resolution or an all-clear.
        assert result.resolved == 0
        assert result.gmail_freshness.status == "stale"
        assert result.warnings
        persisted = store.situations.get_situation(situation.id)
        assert persisted is not None
        assert persisted.state == "pending"
    finally:
        store.close()


def test_degraded_source_rejects_present_empty_snapshot_as_all_clear(
    tmp_path: Path,
) -> None:
    # Given: one Gmail situation from a complete source generation.
    store, engine, clock = _open_engine(tmp_path)
    try:
        thread = situations.InboxThreadSnapshot(
            thread_id="thread",
            latest_message_id="message",
            latest_from_user=False,
            user_is_recipient=True,
            latest_message_at=clock.now() - timedelta(hours=49),
        )
        first = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, (thread,)),
                calendar_events=_calendar_snapshot(store, ()),
            )
        )
        assert first.created == 1
        situation = store.situations.list_situations()[0]

        # When: an explicitly degraded generation supplies an empty snapshot.
        result = engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(
                    store,
                    (),
                    complete=False,
                    warning_codes=("gmail_body_incomplete",),
                ),
                calendar_events=_calendar_snapshot(store, ()),
            )
        )

        # Then: degraded absence cannot resolve the situation or report all-clear.
        assert result.resolved == 0
        assert "gmail: gmail_body_incomplete" in result.warnings
        assert result.gmail_freshness.status == "error"
        assert result.gmail_freshness.error_code == "degraded"
        assert store.source_generation_state("gmail").status == "degraded"
        persisted = store.situations.get_situation(situation.id)
        assert persisted is not None
        assert persisted.state == "pending"
    finally:
        store.close()
