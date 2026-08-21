from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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


def _open_engine(
    tmp_path: Path,
) -> tuple[Store, situations.SituationEngine, FakeClock]:
    require_m3("EngineInputs", "SituationEngine")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    store = Store(tmp_path / "situations.db", clock=clock)
    store.record_sync_success("gmail")
    store.record_sync_success("calendar")
    return store, situations.SituationEngine(store, clock, UTC), clock


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
        inputs = situations.EngineInputs(
            gmail_threads=(),
            calendar_events=_conflicting_events(clock.now()),
        )

        # When: the exact source snapshot is evaluated twice.
        first = engine.evaluate(inputs)
        second = engine.evaluate(inputs)

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
                gmail_threads=(),
                calendar_events=_conflicting_events(clock.now()),
            )
        )
        assert created.created == 1
        situation = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((situation.id,))

        # When: a fresh resync no longer contains the conflict.
        result = engine.evaluate(
            situations.EngineInputs(gmail_threads=(), calendar_events=())
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
            situations.EngineInputs(gmail_threads=(thread,), calendar_events=())
        )
        assert first.created == 1
        situation = store.situations.list_situations()[0]
        clock.advance(timedelta(hours=24))

        # When: the next Gmail pass has no snapshot and freshness is stale.
        result = engine.evaluate(
            situations.EngineInputs(gmail_threads=None, calendar_events=())
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


def test_stale_source_rejects_present_empty_snapshot_as_all_clear(
    tmp_path: Path,
) -> None:
    # Given: one Gmail situation whose last successful sync is now stale.
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
            situations.EngineInputs(gmail_threads=(thread,), calendar_events=())
        )
        assert first.created == 1
        situation = store.situations.list_situations()[0]
        clock.advance(timedelta(hours=24))

        # When: an empty snapshot is supplied without a fresh successful sync.
        result = engine.evaluate(
            situations.EngineInputs(gmail_threads=(), calendar_events=())
        )

        # Then: stale absence cannot resolve the situation or report all-clear.
        assert result.resolved == 0
        assert result.gmail_freshness.status == "stale"
        assert result.warnings
        persisted = store.situations.get_situation(situation.id)
        assert persisted is not None
        assert persisted.state == "pending"
    finally:
        store.close()


def test_stale_gmail_does_not_block_local_personal_occasion(tmp_path: Path) -> None:
    # Given: a D-7 yearly memory while Gmail has no source snapshot.
    store, engine, clock = _open_engine(tmp_path)
    try:
        clock.set(utc_datetime(2026, 7, 11, 9))
        memory = NewMemory(
            kind="fact",
            entity="Mother",
            entity_kind="person",
            attribute="birthday",
            content="Fixture birthday",
            date_anchor="--07-18",
            recurrence="yearly",
            lead_days=7,
        )
        _ = store.remember(memory)

        # When: evaluation skips stale Gmail but evaluates local memory.
        result = engine.evaluate(
            situations.EngineInputs(gmail_threads=None, calendar_events=())
        )

        # Then: the personal occasion is still created with a warning.
        assert result.created == 1
        assert result.warnings
        assert tuple(
            item.situation_type for item in store.situations.list_situations()
        ) == ("personal_occasion",)
    finally:
        store.close()
