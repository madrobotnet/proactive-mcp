from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

import proactive_mcp.store._situation_claim as claim_module
from proactive_mcp.store import (
    DeliveryClaim,
    DeliveryReceiptError,
    Detection,
    InvalidSituationTransitionError,
    Situation,
    SituationEvidence,
    Store,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path


def _detection(
    key: str,
    *,
    expires_at: datetime | None = None,
) -> Detection:
    return Detection(
        situation_type="reply_deadline",
        dedupe_key=key,
        priority="routine",
        title="Fixture reply deadline",
        why_now="Fixture threshold elapsed",
        evidence=SituationEvidence(facts={"source_id": key}),
        expires_at=expires_at,
    )


def _delivery_claim(clock: FakeClock, daily_budget: int) -> DeliveryClaim:
    now = clock.now()
    return DeliveryClaim(
        delivered_at=now.isoformat(),
        cooldown_after=(now - timedelta(hours=24)).isoformat(),
        local_day_start=now.replace(hour=0).isoformat(),
        local_day_end=(now.replace(hour=0) + timedelta(days=1)).isoformat(),
        daily_budget=daily_budget,
        allow_noncritical=True,
    )


def test_store_persists_supported_state_transitions(tmp_path: Path) -> None:
    # Given: pending situations for each terminal transition.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        detections = tuple(
            _detection(key, expires_at=clock.now() if key == "expire" else None)
            for key in ("ack", "mute", "expire", "resolve")
        )
        assert store.situations.upsert_detections(detections).created == 4
        by_key = {
            situation.dedupe_key: situation
            for situation in store.situations.list_situations()
        }

        # When: each valid state path is applied.
        delivered_ack, delivered_mute, _ = store.situations.mark_delivered(
            (
                by_key["ack"].id,
                by_key["mute"].id,
                by_key["expire"].id,
            )
        )
        acknowledged = store.situations.acknowledge_situation(delivered_ack.id)
        muted = store.situations.mute_situation(delivered_mute.id)
        expired_count = store.situations.expire_lapsed()
        resolved_count = store.situations.resolve_absent(
            "reply_deadline",
            {"ack", "mute", "expire"},
        )

        # Then: transitions and their timestamps are durable.
        assert acknowledged.state == "acknowledged"
        assert acknowledged.delivered_at == clock.now().isoformat()
        assert muted.state == "muted"
        assert expired_count == 1
        assert resolved_count == 1
        expired = store.situations.get_situation(by_key["expire"].id)
        resolved = store.situations.get_situation(by_key["resolve"].id)
        assert expired is not None
        assert resolved is not None
        assert expired.state == "expired"
        assert resolved.state == "resolved"


def test_snoozed_situation_returns_to_pending_only_when_due(tmp_path: Path) -> None:
    # Given: a delivered situation snoozed for one hour.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("snooze"),))
        pending = store.situations.list_situations()[0]
        delivered = store.situations.mark_delivered((pending.id,))[0]
        snoozed = store.situations.snooze_situation(
            delivered.id,
            clock.now() + timedelta(hours=1),
        )

        # When: due snoozes are released before and at the exact instant.
        before = store.situations.wake_snoozed()
        clock.advance(timedelta(hours=1))
        at_boundary = store.situations.wake_snoozed()

        # Then: release is inclusive at the specified instant.
        assert snoozed.state == "snoozed"
        assert before == 0
        assert at_boundary == 1
        released = store.situations.get_situation(pending.id)
        assert released is not None
        assert released.state == "pending"


def test_state_transition_rejects_acknowledgement_before_delivery(
    tmp_path: Path,
) -> None:
    # Given: a situation that has never been delivered.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("invalid"),))
        pending = store.situations.list_situations()[0]

        # When/Then: acknowledgement cannot skip the delivered state.
        with pytest.raises(InvalidSituationTransitionError):
            _ = store.situations.acknowledge_situation(pending.id)


def test_state_transition_rejects_snooze_and_mute_before_delivery(
    tmp_path: Path,
) -> None:
    # Given: a situation that has never been delivered.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("invalid"),))
        pending = store.situations.list_situations()[0]

        # When/Then: user actions cannot skip the delivered state.
        with pytest.raises(InvalidSituationTransitionError):
            _ = store.situations.snooze_situation(
                pending.id,
                clock.now() + timedelta(hours=1),
            )
        with pytest.raises(InvalidSituationTransitionError):
            _ = store.situations.mute_situation(pending.id)


def test_resync_dedupes_without_resetting_delivery_state(tmp_path: Path) -> None:
    # Given: one delivered situation.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "situations.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("same-key"),))
        pending = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((pending.id,))

        # When: the same source identity is upserted on resync.
        summary = store.situations.upsert_detections((_detection("same-key"),))
        persisted = store.situations.list_situations()

        # Then: there is one row and delivery ownership is preserved.
        assert summary.created == 0
        assert summary.refreshed == 1
        assert len(persisted) == 1
        assert persisted[0].state == "delivered"


def _race_delivery(path: Path, situation_id: int, barrier: Barrier) -> bool:
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(path, clock=clock) as store:
        assert barrier.wait(timeout=10) >= 0
        try:
            _ = store.situations.mark_delivered((situation_id,))
        except InvalidSituationTransitionError:
            return False
        return True


def test_multi_instance_delivery_claim_succeeds_once(tmp_path: Path) -> None:
    # Given: two server instances racing for one pending situation.
    path = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(path, clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("shared"),))
        situation_id = store.situations.list_situations()[0].id
    barrier = Barrier(2)

    # When: both instances claim delivery at one synchronization point.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(_race_delivery, path, situation_id, barrier)
            for _ in range(2)
        )
    outcomes = tuple(future.result(timeout=10) for future in futures)

    # Then: one process owns delivery and duplicate delivery is rejected.
    assert sorted(outcomes) == [False, True]


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
    assert len(delivered) == 3


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
