from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import DeliveryReceiptError, Store
from tests.situation_store_support import (
    delivery_claim as _delivery_claim,
)
from tests.situation_store_support import (
    detection as _detection,
)
from tests.situation_store_support import (
    receipt_token as _receipt_token,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.store import DeliveryConfirmation


def _database_artifacts(path: Path) -> bytes:
    return b"".join(
        artifact.read_bytes()
        for artifact in path.parent.glob(f"{path.name}*")
        if artifact.is_file()
    )


def test_raw_receipt_is_absent_from_active_and_confirmed_storage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "situations.db"
    receipt_canary = "PR29_RAW_RECEIPT_CANARY_7bJ4wP9mQ2xN6kR8sT3vY5zA1cD0eF"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(path, clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("token-minimization"),))
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 1),
            claim_token=receipt_canary,
            expires_at=clock.now() + timedelta(minutes=2),
        )
        active_dump = "\n".join(store.connection().iterdump())
        assert receipt_canary not in active_dump
        assert receipt_canary.encode() not in _database_artifacts(path)

        confirmation = store.situations.confirm_delivery(reservation.claim_token)
        confirmed_dump = "\n".join(store.connection().iterdump())
        assert (confirmation.status, confirmation.delivered_count) == ("confirmed", 1)
        assert receipt_canary not in confirmed_dump
        assert receipt_canary.encode() not in _database_artifacts(path)


def test_receipt_confirmation_characterizes_first_success(tmp_path: Path) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("first-success"),))
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 1),
            claim_token=_receipt_token("first-success"),
            expires_at=clock.now() + timedelta(minutes=2),
        )

        delivered = store.situations.confirm_delivery(reservation.claim_token)

        assert (delivered.status, delivered.delivered_count) == ("confirmed", 1)
        assert store.situations.count_situations("delivered") == 1


def test_receipt_confirmation_characterizes_unknown_token(tmp_path: Path) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with (
        Store(tmp_path / "situations.db", clock=clock) as store,
        pytest.raises(DeliveryReceiptError),
    ):
        _ = store.situations.confirm_delivery(_receipt_token("unknown"))


def test_receipt_confirmation_characterizes_expired_token(tmp_path: Path) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("expired-token"),))
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 1),
            claim_token=_receipt_token("expired"),
            expires_at=clock.now() + timedelta(minutes=2),
        )
        clock.advance(timedelta(minutes=2))

        with pytest.raises(DeliveryReceiptError):
            _ = store.situations.confirm_delivery(reservation.claim_token)

        assert store.situations.count_situations("pending") == 1


def test_receipt_confirmation_characterizes_one_history_mutation(
    tmp_path: Path,
) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            tuple(
                replace(
                    _detection(f"history-{index}"),
                    situation_type="calendar_conflict",
                )
                for index in range(3)
            )
        )
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 3),
            claim_token=_receipt_token("history"),
            expires_at=clock.now() + timedelta(minutes=2),
        )

        delivered = store.situations.confirm_delivery(reservation.claim_token)

        assert (delivered.status, delivered.delivered_count) == ("confirmed", 3)
        assert store.situations.count_deliveries() == 3


def test_receipt_confirmation_replay_returns_immutable_result(tmp_path: Path) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            tuple(
                replace(
                    _detection(f"replay-{index}"),
                    situation_type="calendar_conflict",
                )
                for index in range(3)
            )
        )
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 3),
            claim_token=_receipt_token("same-process-replay"),
            expires_at=clock.now() + timedelta(minutes=2),
        )

        first = store.situations.confirm_delivery(reservation.claim_token)
        replay = store.situations.confirm_delivery(reservation.claim_token)

        assert (first.status, first.delivered_count) == ("confirmed", 3)
        assert (replay.status, replay.delivered_count) == ("already_confirmed", 3)
        assert store.situations.count_deliveries() == 3


def test_receipt_confirmation_replay_survives_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(path, clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("reopen-replay"),))
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 1),
            claim_token=_receipt_token("reopen-replay"),
            expires_at=clock.now() + timedelta(minutes=2),
        )
        first = store.situations.confirm_delivery(reservation.claim_token)

    with Store(path, clock=clock) as reopened:
        replay = reopened.situations.confirm_delivery(reservation.claim_token)
        assert reopened.situations.count_deliveries() == 1

    assert (first.status, first.delivered_count) == ("confirmed", 1)
    assert (replay.status, replay.delivered_count) == ("already_confirmed", 1)


def _confirm_at_barrier(
    path: Path,
    clock: FakeClock,
    claim_token: str,
    barrier: Barrier,
) -> DeliveryConfirmation:
    with Store(path, clock=clock) as store:
        assert barrier.wait(timeout=10) >= 0
        return store.situations.confirm_delivery(claim_token)


def test_two_stores_concurrently_confirm_one_lease_once(tmp_path: Path) -> None:
    path = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(path, clock=clock) as store:
        _ = store.situations.upsert_detections(
            tuple(
                replace(
                    _detection(f"concurrent-{index}"),
                    situation_type="calendar_conflict",
                )
                for index in range(3)
            )
        )
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 3),
            claim_token=_receipt_token("concurrent-replay"),
            expires_at=clock.now() + timedelta(minutes=2),
        )
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(
                _confirm_at_barrier,
                path,
                clock,
                reservation.claim_token,
                barrier,
            )
            for _ in range(2)
        )
        confirmations = tuple(future.result(timeout=10) for future in futures)

    assert sorted(item.status for item in confirmations) == [
        "already_confirmed",
        "confirmed",
    ]
    assert {item.delivered_count for item in confirmations} == {3}
    with Store(path, clock=clock) as store:
        assert store.situations.count_situations("delivered") == 3
        assert store.situations.count_deliveries() == 3
