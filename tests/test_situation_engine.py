from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from textwrap import dedent
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
    return store, situations.SituationEngine(store, clock, UTC), clock


def _gmail_snapshot(
    store: Store,
    threads: tuple[situations.InboxThreadSnapshot, ...],
    *,
    complete: bool = True,
    warning_codes: tuple[str, ...] = (),
) -> situations.SourceSnapshot[situations.InboxThreadSnapshot]:
    require_m3("SourceSnapshot")
    return situations.SourceSnapshot(
        generation=store.reserve_source_generation("gmail"),
        items=threads,
        complete=complete,
        warning_codes=warning_codes,
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
        assert store.source_generation_state("gmail").status == "degraded"
        persisted = store.situations.get_situation(situation.id)
        assert persisted is not None
        assert persisted.state == "pending"
    finally:
        store.close()


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
            situations.EngineInputs(
                gmail_threads=None,
                calendar_events=_calendar_snapshot(store, ()),
            )
        )

        # Then: the personal occasion is still created with a warning.
        assert result.created == 1
        assert result.warnings
        assert tuple(
            item.situation_type for item in store.situations.list_situations()
        ) == ("personal_occasion",)
    finally:
        store.close()


def test_delayed_source_snapshot_cannot_overwrite_newer_truth(
    tmp_path: Path,
) -> None:
    # Given: two instances reserve Gmail generations before either applies.
    path = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with (
        Store(path, clock=clock) as older_store,
        Store(path, clock=clock) as newer_store,
    ):
        older = _gmail_snapshot(
            older_store,
            (
                situations.InboxThreadSnapshot(
                    thread_id="delayed",
                    latest_message_id="message",
                    latest_from_user=False,
                    user_is_recipient=True,
                    latest_message_at=clock.now() - timedelta(hours=49),
                ),
            ),
        )
        newer = _gmail_snapshot(newer_store, ())

        # When: the newer truth is accepted before the delayed result arrives.
        newest_result = situations.SituationEngine(
            newer_store,
            clock,
            UTC,
        ).evaluate(situations.EngineInputs(gmail_threads=newer))
        delayed_result = situations.SituationEngine(
            older_store,
            clock,
            UTC,
        ).evaluate(situations.EngineInputs(gmail_threads=older))

        # Then: the delayed snapshot is ignored instead of resurrecting old truth.
        assert newest_result.created == 0
        assert delayed_result.created == 0
        assert "gmail: delayed source generation ignored" in delayed_result.warnings
        assert older_store.situations.list_situations() == ()


def test_runtime_factory_wires_detector_and_attention_config(
    tmp_path: Path,
) -> None:
    # Given: config sets detector fallback plus non-default quiet hours.
    clock = FakeClock(utc_datetime(2026, 8, 21, 9))
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        dedent(
            """\
            [attention]
            timezone = "UTC"
            quiet_hours_start = "08:00"
            quiet_hours_end = "10:00"
            [detectors]
            occasion_default_lead_days = 11
            """
        ),
        encoding="utf-8",
    )
    database_path = tmp_path / "situations.db"
    with Store(database_path, clock=clock) as store:
        memory = store.remember(
            NewMemory(
                kind="fact",
                entity="Fixture Person",
                entity_kind="person",
                attribute="birthday",
                content="Fixture birthday",
                date_anchor="--09-01",
                recurrence="yearly",
            )
        )
        with closing(sqlite3.connect(database_path)) as connection:
            _ = connection.execute(
                "UPDATE memory_items SET lead_days = NULL WHERE id = ?",
                (memory.id,),
            )
            connection.commit()

        # When: one production factory wires both engine and attention policy.
        runtime = situations.SituationRuntime.from_config(
            store,
            clock,
            config_path,
        )
        result = runtime.engine.evaluate(
            situations.EngineInputs(
                gmail_threads=_gmail_snapshot(store, ()),
                calendar_events=_calendar_snapshot(store, ()),
            )
        )

        # Then: detector fallback and configured quiet hours both take effect.
        assert result.created == 1
        assert store.situations.list_situations()[0].situation_type == (
            "personal_occasion"
        )
        assert runtime.attention.select_for_delivery(clock.now()) == ()
