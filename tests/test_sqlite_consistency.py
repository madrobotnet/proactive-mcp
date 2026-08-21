from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from proactive_mcp.config import AttentionSettings
from proactive_mcp.situations.policy import AttentionPolicy
from proactive_mcp.store import (
    Detection,
    SituationEvidence,
    SituationPriority,
    SituationType,
    Store,
)
from proactive_mcp.store.situations import (
    DelayedSourceGenerationError,
    DetectionSourceMismatchError,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path


def detection(
    key: str,
    situation_type: SituationType = "reply_deadline",
    priority: SituationPriority = "routine",
) -> Detection:
    return Detection(
        situation_type=situation_type,
        dedupe_key=key,
        priority=priority,
        title=key,
        why_now="test",
        evidence=SituationEvidence(),
    )


def policy(store: Store) -> AttentionPolicy:
    return AttentionPolicy(
        store.situations,
        ZoneInfo("America/New_York"),
        AttentionSettings(
            quiet_hours_start=time(21),
            quiet_hours_end=time(7),
            daily_budget=1,
            cooldown=timedelta(hours=24),
            timezone="America/New_York",
        ),
    )


def test_delayed_generation_rejection_and_whole_batch_rollback(tmp_path: Path) -> None:
    # Given: a newer generation already applied.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        older = store.reserve_source_generation("gmail")
        newer = store.reserve_source_generation("gmail")
        _ = store.situations.apply_source_generation(
            newer, (detection("newer"),), status="complete"
        )
        # When/Then: delayed truth is rejected.
        with pytest.raises(DelayedSourceGenerationError):
            _ = store.situations.apply_source_generation(
                older, (detection("delayed"),), status="complete"
            )
        failing = store.reserve_source_generation("gmail")
        with pytest.raises(DetectionSourceMismatchError):
            _ = store.situations.apply_source_generation(
                failing,
                (detection("rollback"), detection("wrong", "calendar_conflict")),
                status="complete",
            )
        state = store.source_generation_state("gmail")
        assert (state.issued, state.applied, state.status) == (
            failing.number,
            newer.number,
            "complete",
        )
        assert tuple(
            item.dedupe_key for item in store.situations.list_situations()
        ) == ("newer",)


def test_degraded_generation_preserves_absent_source_truth(tmp_path: Path) -> None:
    # Given: one existing Gmail situation and a newly reserved generation.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        first = store.reserve_source_generation("gmail")
        _ = store.situations.apply_source_generation(
            first, (detection("existing"),), status="complete"
        )
        degraded = store.reserve_source_generation("gmail")

        # When: a degraded generation has no detections.
        summary = store.situations.apply_source_generation(
            degraded, (), status="degraded"
        )
        existing = store.situations.list_situations()[0]
        state = store.source_generation_state("gmail")

    # Then: it is accepted but cannot resolve absent source truth.
    assert summary.resolved == 0
    assert existing.state == "pending"
    assert state.applied == degraded.number
    assert state.status == "degraded"


def claim(path: Path, barrier: Barrier) -> tuple[int, ...]:
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(path, clock=clock) as store:
        assert barrier.wait(timeout=10) >= 0
        return tuple(item.id for item in policy(store).claim_for_delivery(clock.now()))


def test_two_stores_atomically_claim_once_and_share_budget(tmp_path: Path) -> None:
    # Given: two candidates and one unit of local-day budget.
    path = tmp_path / "db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(path, clock=clock) as store:
        _ = store.situations.upsert_detections(
            (detection("first"), detection("second"))
        )
    barrier = Barrier(2)
    # When: two stores claim concurrently.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(claim, path, barrier) for _ in range(2))
    results = tuple(future.result(timeout=10) for future in futures)
    # Then: only a successfully claimed row is returned.
    assert sorted(len(result) for result in results) == [0, 1]


def test_budget_uses_immutable_claim_time_priority(tmp_path: Path) -> None:
    # Given: a routine delivery later refreshed to critical.
    now = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(now)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((detection("mutable"),))
        first = policy(store).claim_for_delivery(now)
        _ = store.situations.upsert_detections(
            (detection("mutable", priority="critical"), detection("held"))
        )
        # When: budget is claimed again that local day.
        second = policy(store).claim_for_delivery(now)
    # Then: claim-time routine priority still consumes budget.
    assert tuple(item.dedupe_key for item in first) == ("mutable",)
    assert second == ()


def test_delivered_annual_key_never_reactivates_in_same_year(tmp_path: Path) -> None:
    # Given: this year's occasion was delivered and resolved.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    annual = detection("personal_occasion:entity:4:birthday:2026", "personal_occasion")
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.apply_local_detections((annual,))
        situation = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((situation.id,))
        _ = store.situations.apply_local_detections(())
        # When: the same annual key is detected again.
        summary = store.situations.apply_local_detections((annual,))
        persisted = store.situations.get_situation(situation.id)
    # Then: delivery history suppresses same-year reactivation.
    assert summary.upsert.skipped == 1
    assert persisted is not None
    assert persisted.state == "resolved"


def test_snooze_wake_clears_metadata_and_exempts_cooldown_once(tmp_path: Path) -> None:
    # Given: one delivered situation snoozed for an hour.
    now = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(now)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (detection("snoozed", priority="critical"),)
        )
        situation = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((situation.id,))
        _ = store.situations.snooze_situation(situation.id, now + timedelta(hours=1))
        clock.advance(timedelta(hours=1))
        # When: wake and claim consume the exemption.
        assert store.situations.wake_snoozed() == 1
        woken = store.situations.get_situation(situation.id)
        claimed = policy(store).claim_for_delivery(clock.now())
        _ = store.situations.resolve_absent("reply_deadline", ())
        _ = store.situations.upsert_detections(
            (detection("snoozed", priority="critical"),)
        )
        claimed_again = policy(store).claim_for_delivery(clock.now())
    # Then: stale metadata is clear and exemption was one-shot.
    assert woken is not None
    assert woken.snoozed_until is None
    assert tuple(item.id for item in claimed) == (situation.id,)
    assert claimed_again == ()
