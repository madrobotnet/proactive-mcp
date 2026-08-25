from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, Final

from proactive_mcp.store import Store
from tests.situation_test_support import FakeClock, utc_datetime
from tests.situation_tool_support import (
    DATABASE_NAME,
    RACE_TIMEOUT,
    check_in_worker,
    open_harness,
    pending_detection,
    write_config,
)
from tests.store_migration_support import scalar_int

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.server.situation_responses import ProactiveCheckResponse

_NOON = utc_datetime(2026, 8, 21, 12)
_INSTANCES: Final = 4
_CANDIDATES: Final = 3
_SHARED_BUDGET: Final = 1


def _race(state_directory: Path) -> tuple[ProactiveCheckResponse, ...]:
    """Check from every instance at one shared synchronization point."""
    barrier = Barrier(_INSTANCES)
    with ThreadPoolExecutor(max_workers=_INSTANCES) as executor:
        futures = tuple(
            executor.submit(check_in_worker, state_directory, _NOON, barrier)
            for _ in range(_INSTANCES)
        )
    return tuple(future.result(timeout=RACE_TIMEOUT) for future in futures)


def _recorded_deliveries(state_directory: Path) -> int:
    """Count the immutable delivery rows the race left behind."""
    with Store(state_directory / DATABASE_NAME, clock=FakeClock(_NOON)) as store:
        return scalar_int(
            store.connection(),
            "SELECT COUNT(*) FROM situation_deliveries",
        )


def test_racing_tool_instances_deliver_the_pending_row_exactly_once(
    tmp_path: Path,
) -> None:
    # Given: one pending situation in a database four instances share.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("contested"),)
        )
        contested = harness.store.situations.list_situations(state="pending")[0].id

    # When: every instance checks at once.
    responses = _race(tmp_path)

    # Then: one instance owns the row and exactly one delivery is recorded.
    received = tuple(item.id for response in responses for item in response.situations)
    assert received == (contested,)
    assert _recorded_deliveries(tmp_path) == 1


def test_racing_tool_instances_cannot_overspend_the_shared_daily_budget(
    tmp_path: Path,
) -> None:
    # Given: three routine candidates and one unit of daily budget.
    write_config(tmp_path, daily_budget=_SHARED_BUDGET)
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            tuple(
                pending_detection(f"candidate-{index}") for index in range(_CANDIDATES)
            )
        )

    # When: every instance checks at once.
    responses = _race(tmp_path)

    # Then: the budget is global, so the extra candidates stay pending (§7).
    with open_harness(tmp_path, _NOON) as after:
        pending = after.store.situations.list_situations(state="pending")
        delivered = after.store.situations.list_situations(state="delivered")
    received = tuple(item.id for response in responses for item in response.situations)
    assert len(received) == _SHARED_BUDGET
    assert _recorded_deliveries(tmp_path) == _SHARED_BUDGET
    assert tuple(item.id for item in delivered) == received
    assert len(pending) == _CANDIDATES - _SHARED_BUDGET


def test_a_second_instance_never_redelivers_a_claimed_situation(
    tmp_path: Path,
) -> None:
    # Given: two server instances sharing one database and one situation.
    with open_harness(tmp_path, _NOON) as first:
        _ = first.store.situations.upsert_detections((pending_detection("shared"),))

        # When: the first instance claims it and the second checks next.
        claimed = first.service.proactive_check()
        with open_harness(tmp_path, _NOON) as second:
            repeated = second.service.proactive_check()

    # Then: the claim is not re-offered to the other instance (§5.1).
    assert len(claimed.situations) == 1
    assert repeated.situations == ()
    assert repeated.receipt_token is None
    assert repeated.held_count == 1
    assert repeated.all_clear is False
