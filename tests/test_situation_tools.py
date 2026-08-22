from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest

from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_requests import SituationRequestError
from proactive_mcp.server.situation_responses import (
    ListSituationsResponse,
    ProactiveCheckResponse,
)
from proactive_mcp.server.situation_tools import open_situation_service
from proactive_mcp.sources.lazy_sync import LazySyncPolicy, SourceAccess
from proactive_mcp.store import (
    DaemonStatus,
    DaemonStatusStore,
    InvalidSituationTransitionError,
    SituationNotFoundError,
    Store,
)
from tests.daemon_test_support import (
    FakeCredential,
    FakeCredentialStore,
    FakeReaderFactory,
    StoreBackedReader,
    birthday_memory,
)
from tests.memory_tools_stdio import json_text, memory_session
from tests.situation_test_support import FakeClock, utc_datetime
from tests.situation_tool_support import (
    UNTRUSTED_SUBJECT,
    deliver_one,
    error_text,
    open_harness,
    pending_detection,
    tool_schema,
    write_config,
)
from tests.test_daemon_cli import start_live_overridden_watcher

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.clock import Clock

_NOON = utc_datetime(2026, 8, 21, 12)
_QUIET_NIGHT = utc_datetime(2026, 8, 21, 22)
_BIRTHDAY_MORNING = utc_datetime(2026, 7, 11, 9)
_PRIVATE_MARKER: Final = "PRIVATE-SNOOZE-MARKER"
_TOOL_NAMES: Final = frozenset(
    {
        "proactive_check",
        "list_situations",
        "get_situation",
        "acknowledge_situation",
        "snooze_situation",
        "mute_situation",
    }
)


def test_proactive_check_delivers_the_detected_occasion_exactly_once(
    tmp_path: Path,
) -> None:
    # Given: a D-7 birthday memory and no prior delivery.
    with open_harness(tmp_path, _BIRTHDAY_MORNING) as harness:
        _ = harness.store.remember(birthday_memory())

        # When: the same session checks twice.
        first = harness.service.proactive_check()
        second = harness.service.proactive_check()
        stored = harness.store.situations.list_situations()

    # Then: the situation is received once and never re-offered.
    assert tuple(item.situation_type for item in first.situations) == (
        "personal_occasion",
    )
    assert first.situations[0].state == "delivered"
    assert first.situations[0].priority == "high"
    assert second.situations == ()
    assert tuple(item.state for item in stored) == ("delivered",)


def test_proactive_check_never_reports_all_clear_while_a_source_is_not_ok(
    tmp_path: Path,
) -> None:
    # Given: an installation whose Google setup never ran.
    with open_harness(tmp_path, _NOON) as harness:
        # When: the agent checks and no situation exists.
        response = harness.service.proactive_check()

    # Then: the empty result is never presented as an all-clear (§7).
    assert response.situations == ()
    assert response.all_clear is False
    assert response.freshness.gmail.status == "not_configured"
    assert response.freshness.calendar.status == "not_configured"
    assert "gmail: source is not_configured" in response.warnings
    assert "calendar: source is not_configured" in response.warnings


def test_proactive_check_reports_all_clear_only_when_no_source_warns(
    tmp_path: Path,
) -> None:
    # Given: both sources synced successfully inside the freshness window.
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        harness.store.set_google_auth_state("configured")
        harness.store.record_sync_success("gmail")
        harness.store.record_sync_success("calendar")

        # When: the agent checks with nothing detected.
        response = harness.service.proactive_check()

    # Then: an honest all-clear is allowed and nothing is held back.
    assert response.freshness.gmail.status == "ok"
    assert response.freshness.calendar.status == "ok"
    assert response.warnings == ()
    assert response.all_clear is True
    assert response.held_count == 0


def test_proactive_check_holds_situations_past_the_daily_budget(
    tmp_path: Path,
) -> None:
    # Given: a one-per-day budget and two routine situations.
    write_config(tmp_path, daily_budget=1)
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("first"), pending_detection("second"))
        )

        # When: the agent checks once.
        response = harness.service.proactive_check()

    # Then: the budget caps delivery and the remainder stays pending.
    assert len(response.situations) == 1
    assert response.held_count == 1
    assert response.budget.used == 1
    assert response.budget.remaining == 0
    assert response.budget.daily_budget == 1
    assert response.all_clear is False


def test_proactive_check_delivers_only_critical_inside_quiet_hours(
    tmp_path: Path,
) -> None:
    # Given: 22:00 local quiet hours with one routine and one critical row.
    write_config(tmp_path)
    with open_harness(tmp_path, _QUIET_NIGHT) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("routine"), pending_detection("urgent", "critical"))
        )

        # When: the agent checks during quiet hours.
        response = harness.service.proactive_check()

    # Then: only critical bypasses quiet hours; the rest is held.
    assert tuple(item.priority for item in response.situations) == ("critical",)
    assert response.held_count == 1


def test_list_and_get_isolate_quoted_external_evidence_as_untrusted(
    tmp_path: Path,
) -> None:
    # Given: one pending situation whose evidence quotes external text.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections((pending_detection("evidence"),))

        # When: the agent lists pending situations and reads one detail.
        listed = harness.service.list_situations("pending")
        delivered = harness.service.list_situations("delivered")
        detail = harness.service.get_situation(listed.items[0].id)

    # Then: quoted external text is exposed as marked untrusted data (§9.4).
    assert delivered.items == ()
    assert listed.items == (detail,)
    assert detail.evidence.facts == {"event_a_id": "evidence"}
    assert detail.evidence.quoted_external.trust == "untrusted_external_data"
    assert detail.evidence.quoted_external.values == {"subject": UNTRUSTED_SUBJECT}


def test_get_situation_rejects_an_unknown_id(tmp_path: Path) -> None:
    # Given: an installation with no situations at all.
    with (
        open_harness(tmp_path, _NOON) as harness,
        # When: the agent asks for an id that was never detected.
        # Then: the tool refuses instead of inventing a situation.
        pytest.raises(SituationNotFoundError),
    ):
        _ = harness.service.get_situation(404)


def test_acknowledge_requires_delivery_first(tmp_path: Path) -> None:
    # Given: one detected situation no agent has received yet.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections((pending_detection("ack"),))
        pending = harness.store.situations.list_situations(state="pending")[0]

        # When: acknowledgement is attempted before and after delivery.
        with pytest.raises(InvalidSituationTransitionError):
            _ = harness.service.acknowledge_situation(pending.id)
        delivered = harness.service.proactive_check().situations[0]
        acknowledged = harness.service.acknowledge_situation(delivered.id)

    # Then: only the delivered row may be acknowledged (§5.1).
    assert acknowledged.id == pending.id
    assert acknowledged.state == "acknowledged"


def test_snooze_requires_a_timezone_aware_future_instant(tmp_path: Path) -> None:
    # Given: one delivered situation awaiting a user decision.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "snooze")

        # When: unparseable, naive, and past wake times are offered first.
        with pytest.raises(SituationRequestError) as unparseable:
            _ = harness.service.snooze_situation(delivered.id, _PRIVATE_MARKER)
        with pytest.raises(SituationRequestError):
            _ = harness.service.snooze_situation(delivered.id, "2026-08-21T14:00:00")
        with pytest.raises(SituationRequestError):
            _ = harness.service.snooze_situation(
                delivered.id, "2026-08-21T11:00:00+00:00"
            )
        snoozed = harness.service.snooze_situation(
            delivered.id, "2026-08-21T15:30:00+03:00"
        )

    # Then: only an aware future instant is accepted, normalized to UTC.
    assert _PRIVATE_MARKER not in str(unparseable.value)
    assert snoozed.state == "snoozed"
    assert snoozed.snoozed_until == "2026-08-21T12:30:00+00:00"


def test_snoozed_situation_returns_only_after_its_wake_time(tmp_path: Path) -> None:
    # Given: a delivered situation snoozed two hours out.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "later")
        snoozed = harness.service.snooze_situation(
            delivered.id, "2026-08-21T14:00:00+00:00"
        )

        # When: the agent checks before and after the wake time.
        before = harness.service.proactive_check()
        harness.clock.set(_NOON + timedelta(hours=3))
        after = harness.service.proactive_check()

    # Then: the wake time alone decides re-delivery.
    assert snoozed.state == "snoozed"
    assert before.situations == ()
    assert tuple(item.id for item in after.situations) == (delivered.id,)


def test_mute_instance_keeps_the_type_deliverable(tmp_path: Path) -> None:
    # Given: one delivered situation the user mutes by instance.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "muted-instance")

        # When: the instance is muted and a sibling of its type is detected.
        muted = harness.service.mute_situation(delivered.id, "instance")
        harness.clock.set(_NOON + timedelta(hours=1))
        _ = harness.store.situations.upsert_detections((pending_detection("sibling"),))
        response = harness.service.proactive_check()

    # Then: only that instance is silenced.
    assert muted.scope == "instance"
    assert muted.situation.state == "muted"
    assert muted.muted_types == ()
    assert len(response.situations) == 1
    assert response.situations[0].id != delivered.id


def test_mute_type_atomically_mutes_the_instance_and_its_type(tmp_path: Path) -> None:
    # Given: one delivered situation the user mutes by type.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "muted-type")

        # When: the type is muted and a sibling of its type is detected.
        muted = harness.service.mute_situation(delivered.id, "type")
        harness.clock.set(_NOON + timedelta(hours=1))
        _ = harness.store.situations.upsert_detections((pending_detection("sibling"),))
        response = harness.service.proactive_check()

    # Then: instance and type are muted together and nothing else delivers.
    assert muted.scope == "type"
    assert muted.situation.state == "muted"
    assert muted.muted_types == ("calendar_conflict",)
    assert response.situations == ()
    assert response.held_count == 1


@pytest.mark.anyio
async def test_situation_tools_expose_and_answer_the_m4_surface(tmp_path: Path) -> None:
    # Given: the packaged server running over stdio on an empty database.
    async with memory_session(tmp_path) as session:
        listed = await session.list_tools()

        # When: an agent inspects the surface and calls the delivery tools.
        checked = await session.call_tool("proactive_check")
        pending = await session.call_tool("list_situations", {"state": "pending"})

    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) >= _TOOL_NAMES
    assert tool_schema(tools["proactive_check"]).required == ()
    assert set(tool_schema(tools["list_situations"]).properties) >= {"state"}
    assert set(tool_schema(tools["get_situation"]).required) >= {"id"}
    assert set(tool_schema(tools["acknowledge_situation"]).required) >= {"id"}
    assert set(tool_schema(tools["snooze_situation"]).required) >= {"id", "until"}
    assert set(tool_schema(tools["mute_situation"]).properties) >= {"id", "scope"}

    # Then: both calls answer with the typed contract and no all-clear.
    response = ProactiveCheckResponse.model_validate_json(json_text(checked))
    assert response.situations == ()
    assert response.all_clear is False
    assert response.warnings
    assert ListSituationsResponse.model_validate_json(json_text(pending)).items == ()


@pytest.mark.anyio
async def test_a_refused_snooze_names_the_argument_without_echoing_it(
    tmp_path: Path,
) -> None:
    # Given: the packaged server running over stdio.
    async with memory_session(tmp_path) as session:
        # When: an agent offers a wake time the tool cannot parse.
        refused = await session.call_tool(
            "snooze_situation", {"id": 1, "until": _PRIVATE_MARKER}
        )

    # Then: the agent learns which argument failed, never its value.
    message = error_text(refused)
    assert "until" in message
    assert _PRIVATE_MARKER not in message


@pytest.mark.anyio
async def test_acknowledging_an_unknown_situation_reports_that_over_stdio(
    tmp_path: Path,
) -> None:
    # Given: the packaged server running over stdio on an empty database.
    async with memory_session(tmp_path) as session:
        # When: an agent acknowledges an id that was never detected.
        refused = await session.call_tool("acknowledge_situation", {"id": 404})

    # Then: the store's refusal survives the tool boundary intact.
    assert "404" in error_text(refused)


def test_proactive_check_does_not_inline_read_a_sixty_minute_override_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: configured sources and a still-running daemon started at 60 minutes.
    paths, clock = start_live_overridden_watcher(tmp_path, monkeypatch)
    clock.advance(timedelta(minutes=16))
    seen: list[timedelta | None] = []
    original_status = DaemonStatusStore.status

    def capture_status(
        self: DaemonStatusStore, *, stale_after: timedelta | None = None
    ) -> DaemonStatus:
        seen.append(stale_after)
        return original_status(self, stale_after=stale_after)

    monkeypatch.setattr(DaemonStatusStore, "status", capture_status)
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        reader = StoreBackedReader(store=store)

        def open_access(
            _paths: ProactivePaths, bound: Store, _clock: Clock
        ) -> SourceAccess:
            return SourceAccess(
                sync_state=bound,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )

        monkeypatch.setattr(
            "proactive_mcp.server.situation_tools.open_source_access",
            open_access,
        )
        service = open_situation_service(store, clock, paths)

        # When: proactive_check evaluates lazy-sync 16 minutes after the beat.
        _ = service.proactive_check()

    # Then: liveness uses the 60-minute cadence, so no duplicate inline read.
    assert seen == [
        None,
        LazySyncPolicy.for_poll_interval(timedelta(minutes=60)).daemon_stale_after,
    ]
    assert reader.reads == []


def test_never_started_lazy_sync_uses_configured_poll_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 7-minute config and no daemon start record.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        "[daemon]\npoll_interval_minutes = 7\n",
        encoding="utf-8",
    )
    clock = FakeClock(_NOON)
    seen: list[timedelta | None] = []
    original_status = DaemonStatusStore.status

    def capture_status(
        self: DaemonStatusStore, *, stale_after: timedelta | None = None
    ) -> DaemonStatus:
        seen.append(stale_after)
        return original_status(self, stale_after=stale_after)

    monkeypatch.setattr(DaemonStatusStore, "status", capture_status)
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        reader = StoreBackedReader(store=store)

        def open_access(
            _paths: ProactivePaths, bound: Store, _clock: Clock
        ) -> SourceAccess:
            return SourceAccess(
                sync_state=bound,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )

        monkeypatch.setattr(
            "proactive_mcp.server.situation_tools.open_source_access",
            open_access,
        )
        service = open_situation_service(store, clock, paths)

        # When: proactive_check evaluates lazy-sync with no persisted cadence.
        persisted = store.daemon.status().poll_interval
        _ = service.proactive_check()

    # Then: the configured interval is the never-started liveness fallback.
    assert persisted is None
    assert seen == [
        None,
        None,
        LazySyncPolicy.for_poll_interval(timedelta(minutes=7)).daemon_stale_after,
    ]
    assert reader.reads == [1]
