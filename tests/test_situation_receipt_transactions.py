from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

import proactive_mcp.store._situation_claim as claim_module
from proactive_mcp.store import DeliveryReceiptError, Situation, Store
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


def test_receipt_confirmation_rolls_back_after_first_row_update_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: three pending rows leased by one receipt.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    start = clock.now().replace(hour=0)
    end = start + timedelta(days=1)
    with Store(tmp_path / "situations.db", clock=clock) as store:
        detections = tuple(
            replace(_detection(f"rollback-{index}"), situation_type="calendar_conflict")
            for index in range(3)
        )
        _ = store.situations.upsert_detections(detections)
        claim_token = str(clock.now().timestamp())
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 3),
            claim_token=claim_token,
            expires_at=clock.now() + timedelta(minutes=2),
        )
        original_record_delivery = claim_module.record_delivery

        def fail_after_update(
            connection: sqlite3.Connection,
            situation: Situation,
            timestamp: str,
        ) -> None:
            del connection, situation, timestamp
            raise sqlite3.OperationalError

        monkeypatch.setattr(claim_module, "record_delivery", fail_after_update)

        # When: persistence fails immediately after the first situation update.
        with pytest.raises(sqlite3.OperationalError):
            _ = store.situations.confirm_delivery(reservation.claim_token)

        # Then: state, history, budget reservation, and token all roll back.
        assert store.situations.count_situations("pending") == 3
        assert store.situations.count_situations("delivered") == 0
        assert store.situations.count_deliveries() == 0
        assert store.situations.count_reserved_between(start, end, clock.now()) == 3

        # When: the same receipt is retried after the failure clears.
        monkeypatch.setattr(
            claim_module,
            "record_delivery",
            original_record_delivery,
        )
        delivered = store.situations.confirm_delivery(reservation.claim_token)

    # Then: the retry consumes the token only after all three rows succeed.
    assert (delivered.status, delivered.delivered_count) == ("confirmed", 3)


def test_receipt_confirmation_rejects_partial_success_without_consuming_token(
    tmp_path: Path,
) -> None:
    # Given: one of three leased situations changed before receipt confirmation.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    start = clock.now().replace(hour=0)
    end = start + timedelta(days=1)
    with Store(tmp_path / "situations.db", clock=clock) as store:
        detections = tuple(
            replace(_detection(f"conflict-{index}"), situation_type="calendar_conflict")
            for index in range(3)
        )
        _ = store.situations.upsert_detections(detections)
        claim_token = str(clock.now().timestamp())
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 3),
            claim_token=claim_token,
            expires_at=clock.now() + timedelta(minutes=2),
        )
        first_id = reservation.situations[0].id
        _ = store.situations.mark_delivered((first_id,))

        # When: confirmation cannot transition every row owned by the token.
        with pytest.raises(DeliveryReceiptError):
            _ = store.situations.confirm_delivery(reservation.claim_token)

        # Then: no additional state/history commits and the token remains leased.
        pending_count = store.situations.count_situations("pending")
        delivered_count = store.situations.count_situations("delivered")
        history_count = store.situations.count_deliveries()
        reserved_count = store.situations.count_reserved_between(
            start,
            end,
            clock.now(),
        )

    assert pending_count == 2
    assert delivered_count == 1
    assert history_count == 1
    assert reserved_count == 3


def test_receipt_insert_failure_rolls_back_every_confirmation_mutation(
    tmp_path: Path,
) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    start = clock.now().replace(hour=0)
    end = start + timedelta(days=1)
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            tuple(
                replace(
                    _detection(f"insert-failure-{index}"),
                    situation_type="calendar_conflict",
                )
                for index in range(3)
            )
        )
        reservation = store.situations.reserve_for_delivery(
            _delivery_claim(clock, 3),
            claim_token=_receipt_token("insert-failure"),
            expires_at=clock.now() + timedelta(minutes=2),
        )
        _ = store.connection().execute(
            """
            CREATE TEMP TRIGGER fail_confirmed_receipt_insert
            BEFORE INSERT ON confirmed_delivery_receipts
            BEGIN
                SELECT RAISE(ABORT, 'injected receipt failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="injected receipt failure"):
            _ = store.situations.confirm_delivery(reservation.claim_token)

        assert store.situations.count_situations("pending") == 3
        assert store.situations.count_situations("delivered") == 0
        assert store.situations.count_deliveries() == 0
        assert store.situations.count_reserved_between(start, end, clock.now()) == 3
        _ = store.connection().execute("DROP TRIGGER fail_confirmed_receipt_insert")
        confirmation = store.situations.confirm_delivery(reservation.claim_token)

        assert (confirmation.status, confirmation.delivered_count) == (
            "confirmed",
            3,
        )
