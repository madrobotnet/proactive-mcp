from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING, Final

import pytest

import proactive_mcp.store._situation_consistency as consistency_module
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_requests import SituationRequestError
from proactive_mcp.server.situation_responses import (
    ListSituationsResponse,
    ProactiveCheckResponse,
)
from proactive_mcp.server.situation_tools import (
    SituationToolService,
    open_situation_service,
)
from proactive_mcp.sources.lazy_sync import LazySyncPolicy, SourceAccess
from proactive_mcp.store import (
    DaemonStatus,
    DaemonStatusStore,
    DeliveryReceiptError,
    Detection,
    InvalidSituationTransitionError,
    SituationEvidence,
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
    BarrierClock,
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
    from proactive_mcp.delivery import EvaluationRunner
    from proactive_mcp.delivery.evaluation import EvaluationPass
    from proactive_mcp.store import SituationType

_NOON = utc_datetime(2026, 8, 21, 12)
_QUIET_NIGHT = utc_datetime(2026, 8, 21, 22)
_BIRTHDAY_MORNING = utc_datetime(2026, 7, 11, 9)
_PRIVATE_MARKER: Final = "PRIVATE-SNOOZE-MARKER"
_TOOL_NAMES: Final = frozenset(
    {
        "proactive_check",
        "confirm_delivery",
        "list_situations",
        "get_situation",
        "acknowledge_situation",
        "snooze_situation",
        "mute_situation",
    }
)


@dataclass(slots=True)
class _CountingEvaluation:
    delegate: EvaluationRunner
    calls: int = 0

    def run_once(self) -> EvaluationPass:
        self.calls += 1
        return self.delegate.run_once()


@pytest.mark.anyio
async def test_scheduled_server_exposes_only_unattended_read_tools(
    tmp_path: Path,
) -> None:
    async with memory_session(
        tmp_path,
        server_args=("-m", "proactive_mcp", "serve-scheduled"),
    ) as session:
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}

    assert names == {"confirm_delivery", "get_status", "proactive_check"}


def test_proactive_check_delivers_the_detected_occasion_exactly_once(
    tmp_path: Path,
) -> None:
    # Given: a D-7 birthday memory and no prior delivery.
    with open_harness(tmp_path, _BIRTHDAY_MORNING) as harness:
        _ = harness.store.remember(birthday_memory())

        # When: the same session checks twice.
        first = harness.service.proactive_check()
        assert first.receipt_token is not None
        _ = harness.service.confirm_delivery(first.receipt_token)
        second = harness.service.proactive_check()
        stored = harness.store.situations.list_situations()

    # Then: the situation is received once and never re-offered.
    assert tuple(item.situation_type for item in first.situations) == (
        "personal_occasion",
    )
    assert first.situations[0].state == "pending"
    assert first.situations[0].priority == "high"
    assert second.situations == ()
    assert tuple(item.state for item in stored) == ("delivered",)


def test_rapid_proactive_checks_coalesce_expensive_evaluation(tmp_path: Path) -> None:
    with open_harness(tmp_path, _NOON) as harness:
        evaluation = _CountingEvaluation(harness.dependencies.evaluation)
        service = SituationToolService(
            replace(harness.dependencies, evaluation=evaluation)
        )

        _ = service.proactive_check()
        _ = service.proactive_check()

    assert evaluation.calls == 1


def test_concurrent_daily_and_scheduled_checks_lease_once_then_recover(
    tmp_path: Path,
) -> None:
    # Given: two service instances poised to reserve the same one-row budget.
    write_config(tmp_path, daily_budget=1)
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        harness.store.set_google_auth_state("configured")
        harness.store.record_sync_success("gmail")
        harness.store.record_sync_success("calendar")
        _ = harness.store.situations.upsert_detections(
            (pending_detection("concurrent-lease"),)
        )

    barrier = Barrier(2)

    def check_once() -> ProactiveCheckResponse:
        with open_harness(tmp_path, _NOON, "already_fresh") as harness:
            service = SituationToolService(
                replace(
                    harness.dependencies,
                    clock=BarrierClock(harness.clock, barrier),
                )
            )
            return service.proactive_check()

    # When: daily and scheduled callers race, and the winner exits unconfirmed.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(check_once), executor.submit(check_once))
    responses = tuple(future.result(timeout=20) for future in futures)
    winner = next(response for response in responses if response.situations)
    loser = next(response for response in responses if not response.situations)

    # Then: only the winner owns a token, while the loser sees held work.
    assert winner.receipt_token is not None
    assert loser.receipt_token is None
    assert loser.held_count == 1
    assert loser.all_clear is False

    recovery_time = _NOON + timedelta(minutes=3)
    with open_harness(tmp_path, recovery_time, "already_fresh") as harness:
        assert harness.store.situations.count_deliveries() == 0
        recovered = harness.service.proactive_check()

    assert tuple(item.id for item in recovered.situations) == (winner.situations[0].id,)
    assert recovered.receipt_token is not None
    assert recovered.budget.used == 1


def test_unconfirmed_delivery_lease_expires_back_to_pending(tmp_path: Path) -> None:
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections((pending_detection("receipt"),))

        first = harness.service.proactive_check()
        assert first.receipt_token is not None
        assert harness.store.situations.count_deliveries() == 0
        assert harness.service.get_situation(first.situations[0].id).state == "pending"

        harness.clock.advance(timedelta(minutes=3))
        second = harness.service.proactive_check()
        assert second.receipt_token is not None
        with pytest.raises(DeliveryReceiptError):
            _ = harness.service.confirm_delivery(first.receipt_token)
        confirmation = harness.service.confirm_delivery(second.receipt_token)

    assert tuple(item.id for item in second.situations) == (first.situations[0].id,)
    assert confirmation.delivered_count == 1


def test_one_row_receipt_confirmation_reports_one_delivery(tmp_path: Path) -> None:
    # Given: one pending situation reserved under one receipt token.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("one-row-receipt"),)
        )
        reservation = harness.service.proactive_check()
        assert reservation.receipt_token is not None

        # When: the host confirms that receipt.
        confirmation = harness.service.confirm_delivery(reservation.receipt_token)

        # Then: exactly that row is delivered and recorded once.
        delivered = harness.service.list_situations("delivered")
        delivery_events = harness.store.situations.count_deliveries()

    assert confirmation.delivered_count == 1
    assert len(delivered.items) == 1
    assert delivery_events == 1


def test_receipt_confirmation_rejects_same_token_replay(tmp_path: Path) -> None:
    # Given: one receipt already consumed by a successful confirmation.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("receipt-replay"),)
        )
        reservation = harness.service.proactive_check()
        assert reservation.receipt_token is not None
        _ = harness.service.confirm_delivery(reservation.receipt_token)

        # When/Then: replay cannot deliver or charge the same row again.
        with pytest.raises(DeliveryReceiptError):
            _ = harness.service.confirm_delivery(reservation.receipt_token)
        assert harness.store.situations.count_deliveries() == 1


@pytest.mark.parametrize("situation_count", [1, 3, 100])
def test_receipt_confirmation_reports_every_reserved_situation(
    tmp_path: Path,
    situation_count: int,
) -> None:
    # Given: one receipt holding the configured budget's full candidate set.
    write_config(tmp_path, daily_budget=situation_count)
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            tuple(
                pending_detection(f"multi-receipt-{index}")
                for index in range(situation_count)
            )
        )
        reservation = harness.service.proactive_check()
        assert reservation.receipt_token is not None
        assert len(reservation.situations) == situation_count

        # When: the host confirms the shared receipt once.
        confirmation = harness.service.confirm_delivery(reservation.receipt_token)

        # Then: the response, state, and immutable history agree exactly.
        delivered_count = harness.store.situations.count_situations("delivered")
        delivery_events = harness.store.situations.count_deliveries()

    assert confirmation.delivered_count == situation_count
    assert delivered_count == situation_count
    assert delivery_events == situation_count


@pytest.mark.parametrize(
    "situation_type",
    ["calendar_conflict", "reply_deadline"],
)
def test_unconfirmed_lease_does_not_consume_the_next_local_days_budget(
    tmp_path: Path,
    situation_type: SituationType,
) -> None:
    before_midnight = utc_datetime(2026, 8, 21, 23, 59)
    write_config(
        tmp_path,
        daily_budget=1,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    with open_harness(tmp_path, before_midnight) as harness:
        _ = harness.store.situations.upsert_detections(
            (
                replace(
                    pending_detection("before-midnight"),
                    situation_type=situation_type,
                ),
            )
        )
        first = harness.service.proactive_check()
        assert first.receipt_token is not None

        harness.clock.advance(timedelta(minutes=1))
        _ = harness.store.situations.upsert_detections(
            (
                replace(
                    pending_detection("after-midnight"),
                    situation_type=situation_type,
                ),
            )
        )
        second = harness.service.proactive_check()

    assert tuple(item.evidence.facts["event_a_id"] for item in second.situations) == (
        "after-midnight",
    )
    assert second.budget.used == 1
    assert second.budget.remaining == 0


def test_reply_flood_cannot_starve_non_reply_budget_capacity(tmp_path: Path) -> None:
    write_config(tmp_path, daily_budget=4)
    with open_harness(tmp_path, _NOON) as harness:
        reply_detections = tuple(
            replace(
                pending_detection(f"reply-{index}", "high"),
                situation_type="reply_deadline",
            )
            for index in range(150)
        )
        calendar = pending_detection("trusted-calendar")
        _ = harness.store.situations.upsert_detections((*reply_detections, calendar))

        response = harness.service.proactive_check()

    assert len(response.situations) == 4
    assert (
        sum(item.situation_type == "reply_deadline" for item in response.situations)
        == 3
    )
    assert any(
        item.situation_type == "calendar_conflict" for item in response.situations
    )


def test_proactive_check_excludes_gmail_after_newer_failed_generation(
    tmp_path: Path,
) -> None:
    # Given: a complete Gmail generation produced one pending reply deadline.
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        gmail = Detection(
            situation_type="reply_deadline",
            dedupe_key="gmail-generation-row",
            priority="routine",
            title="Fixture reply deadline",
            why_now="Fixture delivery candidate",
            evidence=SituationEvidence(facts={"thread_id": "generation-thread"}),
        )
        first_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            first_generation,
            (gmail,),
            status="complete",
        )
        _ = harness.store.situations.upsert_detections(
            (pending_detection("independent-calendar"),)
        )

        # When: a newer Gmail generation fails before proactive_check claims rows.
        failed_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            failed_generation,
            (),
            status="degraded",
            error_code="network",
        )
        failed_response = harness.service.proactive_check()
        stored_during_failure = harness.store.situations.list_situations(limit=10)
        failed_state = harness.store.source_generation_state("gmail")
        assert failed_response.receipt_token is not None
        _ = harness.service.confirm_delivery(failed_response.receipt_token)

        # When: the same Gmail truth returns in a later complete generation.
        recovery_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            recovery_generation,
            (gmail,),
            status="complete",
        )
        recovered = harness.service.proactive_check()
        assert recovered.receipt_token is not None
        _ = harness.service.confirm_delivery(recovered.receipt_token)
        repeated = harness.service.proactive_check()
        stored_after_recovery = harness.store.situations.list_situations(limit=10)

    # Then: failure gates only Gmail; recovery offers its preserved row exactly once.
    assert (first_generation.number, failed_generation.number) == (1, 2)
    assert (failed_state.issued, failed_state.applied, failed_state.status) == (
        2,
        2,
        "degraded",
    )
    assert tuple(item.situation_type for item in failed_response.situations) == (
        "calendar_conflict",
    )
    assert failed_response.held_count == 1
    assert failed_response.warnings
    assert failed_response.all_clear is False
    assert {item.situation_type for item in stored_during_failure} == {
        "reply_deadline",
        "calendar_conflict",
    }
    assert tuple(item.situation_type for item in recovered.situations) == (
        "reply_deadline",
    )
    assert recovery_generation.number == 3
    assert repeated.situations == ()
    assert (
        sum(item.situation_type == "reply_deadline" for item in stored_after_recovery)
        == 1
    )


def test_proactive_check_excludes_gmail_during_interrupted_newer_generation(
    tmp_path: Path,
) -> None:
    # Given: complete Gmail truth and an independent Calendar row.
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        gmail = Detection(
            situation_type="reply_deadline",
            dedupe_key="interrupted-gmail-row",
            priority="routine",
            title="Fixture reply deadline",
            why_now="Fixture delivery candidate",
            evidence=SituationEvidence(facts={"thread_id": "interrupted-thread"}),
        )
        complete_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            complete_generation,
            (gmail,),
            status="complete",
        )
        _ = harness.store.situations.upsert_detections(
            (pending_detection("interrupted-calendar"),)
        )

        # When: the next generation is reserved but interrupted before acceptance.
        interrupted_generation = harness.store.reserve_source_generation("gmail")
        response = harness.service.proactive_check()
        generation_state = harness.store.source_generation_state("gmail")
        stored = harness.store.situations.list_situations(limit=10)

    # Then: prior accepted Gmail truth cannot cross the in-flight generation.
    assert (complete_generation.number, interrupted_generation.number) == (1, 2)
    assert (
        generation_state.issued,
        generation_state.applied,
        generation_state.status,
    ) == (2, 1, "complete")
    assert tuple(item.situation_type for item in response.situations) == (
        "calendar_conflict",
    )
    assert sum(item.situation_type == "reply_deadline" for item in stored) == 1


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


def test_proactive_check_warns_when_situation_capacity_rejects_a_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consistency_module, "_MAX_SITUATION_ROWS", 0)
    with open_harness(tmp_path, _BIRTHDAY_MORNING, "already_fresh") as harness:
        harness.store.set_google_auth_state("configured")
        harness.store.record_sync_success("gmail")
        harness.store.record_sync_success("calendar")
        _ = harness.store.remember(birthday_memory())

        response = harness.service.proactive_check()

    assert response.situations == ()
    assert response.held_count == 0
    assert response.all_clear is False
    assert any(
        warning.startswith("situations: persistence capacity rejected 1 detection")
        for warning in response.warnings
    )


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
    assert detail.evidence.quoted_memory.trust == "untrusted_memory_data"
    assert detail.evidence.quoted_memory.values == {}


def test_list_situations_is_cursor_paginated_and_stable(tmp_path: Path) -> None:
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            tuple(pending_detection(f"page-{index}") for index in range(25))
        )

        first = harness.service.list_situations(limit=10)
        assert first.next_after_id is not None
        second = harness.service.list_situations(
            after_id=first.next_after_id,
            limit=10,
        )

    first_ids = tuple(item.id for item in first.items)
    second_ids = tuple(item.id for item in second.items)
    assert len(first_ids) == len(second_ids) == 10
    assert set(first_ids).isdisjoint(second_ids)
    assert max(first_ids) < min(second_ids)


def test_situation_row_quota_skips_new_remote_id_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consistency_module, "_MAX_SITUATION_ROWS", 1)
    with open_harness(tmp_path, _NOON) as harness:
        summary = harness.store.situations.upsert_detections(
            (pending_detection("first"), pending_detection("second"))
        )

        count = harness.store.situations.count_situations()

    assert summary.created == 1
    assert summary.skipped == 1
    assert summary.capacity_skipped == 1
    assert count == 1


def test_situation_record_quota_skips_oversized_external_evidence(
    tmp_path: Path,
) -> None:
    oversized = replace(
        pending_detection("oversized"),
        evidence=SituationEvidence(quoted_external={"subject": "x" * 20_000}),
    )
    with open_harness(tmp_path, _NOON) as harness:
        summary = harness.store.situations.upsert_detections((oversized,))

        count = harness.store.situations.count_situations()

    assert summary.created == 0
    assert summary.skipped == 1
    assert summary.capacity_skipped == 1
    assert count == 0


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
        response = harness.service.proactive_check()
        assert response.receipt_token is not None
        _ = harness.service.confirm_delivery(response.receipt_token)
        delivered = response.situations[0]
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
    assert set(tool_schema(tools["confirm_delivery"]).required) >= {"receipt_token"}
    assert set(tool_schema(tools["list_situations"]).properties) >= {
        "after_id",
        "limit",
        "state",
    }
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
