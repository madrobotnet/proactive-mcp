from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import DeliveryReceiptError
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import open_harness, pending_detection, write_config

if TYPE_CHECKING:
    from pathlib import Path

_NOON = utc_datetime(2026, 8, 21, 12)


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


def test_receipt_confirmation_replay_returns_typed_success(tmp_path: Path) -> None:
    # Given: one receipt already consumed by a successful confirmation.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("receipt-replay"),)
        )
        reservation = harness.service.proactive_check()
        assert reservation.receipt_token is not None
        first = harness.service.confirm_delivery(reservation.receipt_token)

        # When: the host replays the same token.
        replay = harness.service.confirm_delivery(reservation.receipt_token)

        # Then: both calls succeed with one immutable delivery result.
        assert (first.status, first.delivered_count) == ("confirmed", 1)
        assert (replay.status, replay.delivered_count) == (
            "already_confirmed",
            1,
        )
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
