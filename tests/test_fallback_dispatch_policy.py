from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING

from proactive_mcp.config import FallbackSettings
from proactive_mcp.delivery.fallback import FallbackSent
from proactive_mcp.store import Store
from tests.fallback_test_support import (
    CANARY_SENDER,
    CANARY_SUBJECT,
    NOTIFY,
    NOW,
    POISON_TITLE,
    SECOND,
    WAIT,
    ProbingRunner,
    RecordingRunner,
    aged,
    by_key,
    claimed,
    delivery,
    detection,
    dispatch,
    dispatch_in_worker,
    poisoned,
)
from tests.situation_test_support import FakeClock
from tests.store_migration_support import scalar_int

if TYPE_CHECKING:
    from pathlib import Path


def test_configured_priorities_override_the_critical_default(tmp_path: Path) -> None:
    # Given: one high and one critical situation past the wait.
    with aged(
        tmp_path,
        detection("high-row", priority="high"),
        detection("critical-row"),
    ) as store:
        runner = RecordingRunner()
        high = by_key(store, "high-row")

        # When: config selects high only, overriding the critical default.
        dispatched = dispatch(store, runner, FallbackSettings(priorities=("high",)))

        # Then: the configured priority alone reaches the OS channel.
        assert dispatched == (FallbackSent(high.id),)
        assert claimed(store) == ("high-row",)


def test_high_and_routine_are_never_toasted_by_default(tmp_path: Path) -> None:
    # Given: aged high and routine situations and no critical one.
    with aged(
        tmp_path,
        detection("high-row", priority="high"),
        detection("routine-row", priority="routine"),
    ) as store:
        runner = RecordingRunner()

        # When: the dispatcher runs with default settings.
        dispatched = dispatch(store, runner)

        # Then: only critical falls back, per the product plan.
        assert dispatched == ()
        assert runner.calls == []
        assert claimed(store) == ()


def test_muted_situation_type_is_never_toasted(tmp_path: Path) -> None:
    # Given: an aged critical situation whose type the user muted.
    with aged(
        tmp_path,
        detection("muted-first"),
        detection("after-mute"),
    ) as store:
        muted = by_key(store, "muted-first")
        _ = store.situations.mark_delivered((muted.id,))
        _ = store.situations.mute_situation_type(muted.id)
        runner = RecordingRunner()

        # When: the dispatcher runs after the wait elapsed.
        dispatched = dispatch(store, runner)

        # Then: a muted type stays silent on the OS channel too.
        assert dispatched == ()
        assert runner.calls == []
        assert claimed(store) == ()


def test_lapsed_situation_is_never_toasted(tmp_path: Path) -> None:
    # Given: an aged critical situation whose relevance window closed.
    with aged(
        tmp_path,
        detection("lapsed", expires_at=NOW - SECOND),
    ) as store:
        runner = RecordingRunner()

        # When: the dispatcher runs after that window passed.
        dispatched = dispatch(store, runner)

        # Then: an irrelevant situation is never toasted.
        assert dispatched == ()
        assert runner.calls == []
        assert claimed(store) == ()


def test_toast_is_sent_outside_the_claim_transaction(tmp_path: Path) -> None:
    # Given: two aged critical situations and a second live connection.
    with (
        aged(tmp_path, detection("dispatched"), detection("probed")) as store,
        Store(tmp_path / "db", busy_timeout_ms=0, clock=FakeClock(NOW)) as probe,
    ):
        runner = ProbingRunner(probe)
        dispatched_id = by_key(store, "dispatched").id
        probed_id = by_key(store, "probed").id

        # When: a second dispatcher runs while the first send is in flight.
        dispatched = dispatch(store, runner)

        # Then: no write lock is held while the notification runs.
        assert dispatched == (FallbackSent(dispatched_id),)
        assert runner.concurrent == [FallbackSent(probed_id)]


def test_two_dispatchers_toast_one_situation_once(tmp_path: Path) -> None:
    # Given: one aged critical situation and two dispatchers held together.
    with aged(tmp_path, detection("contested")) as store:
        assert claimed(store) == ()
    barrier = Barrier(2)

    # When: both dispatchers run from the same synchronization point.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(dispatch_in_worker, tmp_path, barrier) for _ in range(2)
        )
    outcomes = tuple(future.result(timeout=20) for future in futures)

    # Then: exactly one dispatcher owns the single toast.
    with Store(tmp_path / "db", clock=FakeClock(NOW)) as store:
        rows = scalar_int(
            store.connection(),
            "SELECT COUNT(*) FROM situation_fallbacks",
        )
    assert sorted(outcomes) == [(0, 0), (1, 1)]
    assert rows == 1


def test_agent_delivery_before_the_boundary_prevents_the_toast(tmp_path: Path) -> None:
    # Given: a situation an agent received before the wait elapsed.
    clock = FakeClock(NOW - WAIT)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((detection("agent-first"),))
        clock.set(NOW - timedelta(minutes=15))
        delivered = store.situations.claim_for_delivery(delivery(clock.now()))
        clock.set(NOW)
        runner = RecordingRunner()

        # When: the dispatcher reaches the fallback boundary afterwards.
        dispatched = dispatch(store, runner)

        # Then: agent delivery wins and no OS notification is claimed.
        assert tuple(item.dedupe_key for item in delivered) == ("agent-first",)
        assert dispatched == ()
        assert runner.calls == []
        assert claimed(store) == ()


def test_toast_leaves_the_situation_pending_for_agent_delivery(tmp_path: Path) -> None:
    # Given: an aged critical situation no agent received.
    with aged(tmp_path, detection("still-pending")) as store:
        runner = RecordingRunner()

        # When: the dispatcher notifies the OS.
        dispatched = dispatch(store, runner)
        situation = by_key(store, "still-pending")

        # Then: an OS notification is not an agent delivery.
        assert dispatched == (FallbackSent(situation.id),)
        assert (situation.state, situation.delivered_at) == ("pending", None)


def test_agent_may_deliver_a_toasted_situation(tmp_path: Path) -> None:
    # Given: a situation whose one OS notification was already sent.
    with aged(tmp_path, detection("toasted")) as store:
        runner = RecordingRunner()
        dispatched = dispatch(store, runner)

        # When: an agent checks in after the toast.
        delivered = store.situations.claim_for_delivery(delivery(NOW))
        history = store.fallbacks.history(by_key(store, "toasted").id)

        # Then: proactive_check still delivers it and the toast stays one-shot.
        assert tuple(item.dedupe_key for item in delivered) == ("toasted",)
        assert delivered[0].state == "delivered"
        assert dispatched == (FallbackSent(delivered[0].id),)
        assert history is not None
        assert history.outcome == "sent"


def test_quoted_external_poison_never_reaches_the_notifier(tmp_path: Path) -> None:
    # Given: an aged critical situation carrying quoted external canaries.
    with aged(tmp_path, poisoned()) as store:
        runner = RecordingRunner()
        situation = by_key(store, "poisoned")

        # When: the dispatcher notifies the OS.
        dispatched = dispatch(store, runner)

        # Then: only the isolated payload fields cross the boundary.
        assert dispatched == (FallbackSent(situation.id),)
        assert runner.calls == [(*NOTIFY, "Calendar conflict", "calendar_conflict")]
        argv = "\0".join(runner.calls[0])
        assert POISON_TITLE not in argv
        assert CANARY_SUBJECT not in argv
        assert CANARY_SENDER not in argv
