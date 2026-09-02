from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.server.situation_tools import SituationToolService
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import (
    BarrierClock,
    open_harness,
    pending_detection,
    write_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.server.situation_responses import ProactiveCheckResponse
    from proactive_mcp.store import SituationType

_NOON = utc_datetime(2026, 8, 21, 12)
_QUIET_NIGHT = utc_datetime(2026, 8, 21, 22)
_AFTER_QUIET_HOURS = utc_datetime(2026, 8, 22, 7)


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


def test_quiet_hours_hold_carries_over_to_next_check(tmp_path: Path) -> None:
    # Given: a routine situation held during the default quiet-hours window.
    write_config(tmp_path)
    with open_harness(tmp_path, _QUIET_NIGHT) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("overnight"),)
        )
        held = harness.service.proactive_check()

    assert held.situations == ()
    assert held.held_count == 1
    assert held.receipt_token is None

    # When: the host checks again at the 07:00 end boundary the next morning.
    with open_harness(tmp_path, _AFTER_QUIET_HOURS) as harness:
        resurfaced = harness.service.proactive_check()

    # Then: the same pending situation is leased instead of being dropped overnight.
    assert tuple(
        item.evidence.facts["event_a_id"] for item in resurfaced.situations
    ) == ("overnight",)
    assert resurfaced.held_count == 0
    assert resurfaced.receipt_token is not None
