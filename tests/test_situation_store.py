from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import (
    Detection,
    InvalidSituationTransitionError,
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
