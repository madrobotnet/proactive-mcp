from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from proactive_mcp.store import (
    DeliveryClaim,
    DeliveryReceiptError,
    Detection,
    FallbackClaim,
    FallbackNotClaimedError,
    Situation,
    SituationEvidence,
    SituationPriority,
    Store,
)
from tests.situation_test_support import FakeClock, utc_datetime
from tests.store_migration_support import scalar_int

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_NOW = utc_datetime(2026, 8, 21, 16)
_WAIT = timedelta(minutes=30)


def _conflict(
    key: str,
    *,
    priority: SituationPriority = "critical",
    expires_at: datetime | None = None,
) -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=key,
        priority=priority,
        title="Fixture conflict",
        why_now="Fixture starts within two hours",
        evidence=SituationEvidence(facts={"source_id": key}),
        expires_at=expires_at,
    )


def _reply(key: str) -> Detection:
    return Detection(
        situation_type="reply_deadline",
        dedupe_key=key,
        priority="critical",
        title="Fixture reply deadline",
        why_now="Fixture threshold elapsed",
        evidence=SituationEvidence(facts={"source_id": key}),
    )


def _claim(
    now: datetime,
    *,
    priorities: tuple[SituationPriority, ...] = ("critical",),
) -> FallbackClaim:
    return FallbackClaim(
        claimed_at=now.isoformat(),
        detected_before=(now - _WAIT).isoformat(),
        priorities=priorities,
    )


def _delivery(now: datetime) -> DeliveryClaim:
    return DeliveryClaim(
        delivered_at=now.isoformat(),
        cooldown_after=(now - timedelta(hours=24)).isoformat(),
        local_day_start=(now - timedelta(hours=16)).isoformat(),
        local_day_end=(now + timedelta(hours=8)).isoformat(),
        daily_budget=4,
        allow_noncritical=True,
    )


def _by_key(store: Store, key: str) -> Situation:
    found = tuple(
        item for item in store.situations.list_situations() if item.dedupe_key == key
    )
    assert len(found) == 1
    return found[0]


def _mute_type(store: Store, key: str) -> None:
    _ = store.situations.upsert_detections((_conflict(key),))
    muted = _by_key(store, key)
    _ = store.situations.mark_delivered((muted.id,))
    _ = store.situations.mute_situation_type(muted.id)


def _claim_in_worker(path: Path, barrier: Barrier) -> int | None:
    clock = FakeClock(_NOW)
    with Store(path, clock=clock) as store:
        assert barrier.wait(timeout=10) >= 0
        claimed = store.fallbacks.claim_next(_claim(clock.now()))
        return None if claimed is None else claimed.id


def test_candidates_require_configured_priority_and_elapsed_wait(
    tmp_path: Path,
) -> None:
    # Given: rows differing only in detection age and priority.
    clock = FakeClock(_NOW - _WAIT - timedelta(minutes=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (_conflict("waited"), _conflict("routine-waited", priority="routine"))
        )
        clock.set(_NOW - timedelta(minutes=10))
        _ = store.situations.upsert_detections((_conflict("too-recent"),))

        # When: candidates are read for the configured critical-only fallback.
        candidates = store.fallbacks.candidates(_claim(_NOW))

    # Then: only a configured priority past the wait boundary qualifies.
    assert tuple(item.dedupe_key for item in candidates) == ("waited",)


def test_empty_configured_priorities_disable_the_fallback(tmp_path: Path) -> None:
    # Given: an otherwise eligible critical row.
    clock = FakeClock(_NOW - timedelta(hours=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("eligible"),))
        clock.set(_NOW)

        # When: no priority is configured for OS notification fallback.
        disabled = _claim(_NOW, priorities=())
        candidates = store.fallbacks.candidates(disabled)
        claimed = store.fallbacks.claim_next(disabled)

    # Then: the fallback stays silent instead of defaulting to every row.
    assert candidates == ()
    assert claimed is None


def test_candidates_exclude_muted_expired_and_delivered_rows(tmp_path: Path) -> None:
    # Given: one eligible row beside a muted, a lapsed, and a delivered row.
    clock = FakeClock(_NOW - _WAIT - timedelta(minutes=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _mute_type(store, "muted-type")
        _ = store.situations.upsert_detections(
            (
                _reply("eligible"),
                _conflict("muted-instance"),
                _conflict("lapsed", expires_at=_NOW - timedelta(seconds=1)),
                _conflict("agent-delivered"),
            )
        )
        _ = store.situations.mark_delivered((_by_key(store, "agent-delivered").id,))

        # When: candidates are read at the fallback boundary.
        candidates = store.fallbacks.candidates(_claim(_NOW))

    # Then: every suppressed row is withheld from the OS notification path.
    assert tuple(item.dedupe_key for item in candidates) == ("eligible",)


def test_woken_situation_with_delivery_history_is_never_a_candidate(
    tmp_path: Path,
) -> None:
    # Given: a row an agent already received, snoozed, and woken back to pending.
    clock = FakeClock(_NOW - timedelta(hours=2))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("already-seen"),))
        situation = _by_key(store, "already-seen")
        _ = store.situations.mark_delivered((situation.id,))
        _ = store.situations.snooze_situation(situation.id, _NOW - timedelta(hours=1))
        clock.set(_NOW)
        assert store.situations.wake_snoozed() == 1

        # When: the fallback path evaluates the pending row again.
        candidates = store.fallbacks.candidates(_claim(_NOW))

    # Then: immutable delivery history keeps the fallback silent.
    assert candidates == ()


def test_claim_is_one_shot_and_never_retried(tmp_path: Path) -> None:
    # Given: one eligible critical row.
    clock = FakeClock(_NOW - timedelta(hours=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("only-candidate"),))
        clock.set(_NOW)

        # When: the same boundary is evaluated twice.
        first = store.fallbacks.claim_next(_claim(_NOW))
        second = store.fallbacks.claim_next(_claim(_NOW))
        remaining = store.fallbacks.candidates(_claim(_NOW))
        assert first is not None
        history = store.fallbacks.history(first.id)

    # Then: the claim is recorded once, before any send, and never reoffered.
    assert first.dedupe_key == "only-candidate"
    assert second is None
    assert remaining == ()
    assert history is not None
    assert (history.outcome, history.claimed_at, history.priority) == (
        "claimed",
        _NOW.isoformat(),
        "critical",
    )
    assert history.completed_at is None


def test_recorded_send_completes_the_claim_with_its_own_timestamp(
    tmp_path: Path,
) -> None:
    # Given: a claimed fallback awaiting its notification result.
    clock = FakeClock(_NOW - timedelta(hours=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("to-send"),))
        clock.set(_NOW)
        claimed = store.fallbacks.claim_next(_claim(_NOW))
        assert claimed is not None

        # When: the notification subprocess succeeds later.
        clock.advance(timedelta(seconds=2))
        store.fallbacks.record_sent(claimed.id)
        record = store.fallbacks.history(claimed.id)

    # Then: the outcome and completion instant are persisted once.
    assert record is not None
    assert record.outcome == "sent"
    assert record.completed_at == (_NOW + timedelta(seconds=2)).isoformat()
    assert record.failure_code is None


def test_recorded_failure_keeps_an_enumerated_reason(tmp_path: Path) -> None:
    # Given: a claimed fallback whose notification tool is missing.
    clock = FakeClock(_NOW - timedelta(hours=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("to-fail"),))
        clock.set(_NOW)
        claimed = store.fallbacks.claim_next(_claim(_NOW))
        assert claimed is not None

        # When: the failure is recorded.
        store.fallbacks.record_failed(claimed.id, code="tool_missing")
        record = store.fallbacks.history(claimed.id)

        # Then: the reason is an enum and no retry outcome may follow.
        assert record is not None
        assert (record.outcome, record.failure_code) == ("failed", "tool_missing")
        with pytest.raises(FallbackNotClaimedError):
            store.fallbacks.record_sent(claimed.id)


def test_outcome_without_a_claim_is_rejected(tmp_path: Path) -> None:
    # Given: an eligible row that was never claimed.
    clock = FakeClock(_NOW - timedelta(hours=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("unclaimed"),))
        situation = _by_key(store, "unclaimed")
        clock.set(_NOW)

        # When/Then: an outcome cannot exist without its claim.
        with pytest.raises(FallbackNotClaimedError):
            store.fallbacks.record_sent(situation.id)
        assert store.fallbacks.history(situation.id) is None


def test_two_daemon_instances_claim_one_fallback_once(tmp_path: Path) -> None:
    # Given: one eligible row and two daemon instances released together.
    path = tmp_path / "db"
    clock = FakeClock(_NOW - timedelta(hours=1))
    with Store(path, clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("contested"),))
    barrier = Barrier(2)

    # When: both instances claim at the same synchronization point.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(_claim_in_worker, path, barrier) for _ in range(2)
        )
    outcomes = tuple(future.result(timeout=20) for future in futures)

    # Then: exactly one instance owns the single one-shot history row.
    with Store(path, clock=FakeClock(_NOW)) as store:
        rows = scalar_int(
            store.connection(),
            "SELECT COUNT(*) FROM situation_fallbacks",
        )
    assert sorted(item is None for item in outcomes) == [False, True]
    assert rows == 1


def test_agent_delivery_before_the_boundary_suppresses_the_fallback(
    tmp_path: Path,
) -> None:
    # Given: a row an agent claims before the fallback wait elapses.
    clock = FakeClock(_NOW - _WAIT - timedelta(minutes=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("agent-first"),))
        clock.set(_NOW - timedelta(minutes=15))
        delivered = store.situations.claim_for_delivery(_delivery(clock.now()))
        clock.set(_NOW)

        # When: the fallback boundary is reached afterwards.
        candidates = store.fallbacks.candidates(_claim(_NOW))
        claimed = store.fallbacks.claim_next(_claim(_NOW))

    # Then: agent delivery wins and no OS notification is claimed.
    assert tuple(item.dedupe_key for item in delivered) == ("agent-first",)
    assert candidates == ()
    assert claimed is None


def test_unconfirmed_agent_lease_defers_fallback_until_it_expires(
    tmp_path: Path,
) -> None:
    # Given: an aged critical row leased to an agent without a receipt.
    clock = FakeClock(_NOW - _WAIT - timedelta(minutes=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("leased"),))
        clock.set(_NOW)
        reservation = store.situations.reserve_for_delivery(
            _delivery(_NOW),
            claim_token=uuid4().hex,
            expires_at=_NOW + timedelta(minutes=2),
        )

        # When: fallback checks before and after the unconfirmed lease expires.
        active_candidates = store.fallbacks.candidates(_claim(_NOW))
        clock.advance(timedelta(minutes=3))
        fallback = store.fallbacks.claim_next(_claim(clock.now()))
        with pytest.raises(DeliveryReceiptError):
            _ = store.situations.confirm_delivery(reservation.claim_token)

        # Then: the active host lease cannot race the fallback, but an abandoned
        # lease cannot suppress the safety notification or create delivery history.
        assert active_candidates == ()
        assert fallback is not None
        assert fallback.dedupe_key == "leased"
        assert store.situations.count_deliveries() == 0


def test_fallback_claim_leaves_the_row_deliverable_to_agents(tmp_path: Path) -> None:
    # Given: a row the fallback path claimed at its boundary.
    clock = FakeClock(_NOW - _WAIT - timedelta(minutes=1))
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_conflict("fallback-first"),))
        clock.set(_NOW)
        fallback = store.fallbacks.claim_next(_claim(_NOW))
        assert fallback is not None
        store.fallbacks.record_sent(fallback.id)

        # When: an agent checks in after the OS notification was sent.
        delivered = store.situations.claim_for_delivery(_delivery(_NOW))
        history = store.fallbacks.history(fallback.id)

    # Then: the situation is still delivered and the fallback stays one-shot.
    assert tuple(item.dedupe_key for item in delivered) == ("fallback-first",)
    assert history is not None
    assert history.outcome == "sent"
