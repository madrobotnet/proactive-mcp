from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import DaemonNotStartedError, Store
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

_STALE_AFTER = timedelta(minutes=5)


def test_daemon_status_reports_never_started_before_any_heartbeat(
    tmp_path: Path,
) -> None:
    # Given: a database no daemon has ever written to.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        # When: liveness is evaluated.
        status = store.daemon.status(stale_after=_STALE_AFTER)

    # Then: degraded mode is explicit rather than implied by a missing row.
    assert status.liveness == "never_started"
    assert status.pid is None
    assert status.heartbeat_at is None
    assert status.cycle_count == 0


def test_daemon_start_and_heartbeats_report_running_with_cycle_counts(
    tmp_path: Path,
) -> None:
    # Given: a started daemon.
    started = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(started)
    with Store(tmp_path / "db", clock=clock) as store:
        store.daemon.record_start(pid=4242)

        # When: two evaluation cycles complete.
        clock.advance(timedelta(minutes=1))
        store.daemon.record_heartbeat()
        clock.advance(timedelta(minutes=1))
        store.daemon.record_heartbeat()
        status = store.daemon.status(stale_after=_STALE_AFTER)

    # Then: liveness, identity, and cycle count come from the injected clock.
    assert status.liveness == "running"
    assert status.pid == 4242
    assert status.started_at == started.isoformat()
    assert status.heartbeat_at == (started + timedelta(minutes=2)).isoformat()
    assert status.cycle_count == 2


def test_daemon_status_turns_stale_only_past_the_heartbeat_threshold(
    tmp_path: Path,
) -> None:
    # Given: a daemon whose last heartbeat is exactly at the threshold.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        store.daemon.record_start(pid=4242)
        clock.advance(_STALE_AFTER)
        at_threshold = store.daemon.status(stale_after=_STALE_AFTER)

        # When: one more second elapses without a heartbeat.
        clock.advance(timedelta(seconds=1))
        past_threshold = store.daemon.status(stale_after=_STALE_AFTER)

    # Then: staleness begins after the threshold, not at it.
    assert at_threshold.liveness == "running"
    assert past_threshold.liveness == "stale"


def test_daemon_stop_reports_stopped_inside_the_heartbeat_window(
    tmp_path: Path,
) -> None:
    # Given: a daemon that just wrote a heartbeat.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        store.daemon.record_start(pid=4242)
        store.daemon.record_heartbeat()

        # When: it shuts down cleanly.
        store.daemon.record_stop()
        status = store.daemon.status(stale_after=_STALE_AFTER)

    # Then: a fresh heartbeat does not make a stopped daemon look alive.
    assert status.liveness == "stopped"
    assert status.cycle_count == 1


def test_daemon_restart_resets_cycle_counts_and_start_time(tmp_path: Path) -> None:
    # Given: a daemon that ran one cycle and stopped.
    first_start = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(first_start)
    with Store(tmp_path / "db", clock=clock) as store:
        store.daemon.record_start(pid=4242)
        store.daemon.record_heartbeat()
        store.daemon.record_stop()

        # When: a new daemon process starts.
        clock.advance(timedelta(hours=1))
        store.daemon.record_start(pid=5353)
        status = store.daemon.status(stale_after=_STALE_AFTER)

    # Then: the new process owns the liveness row from zero.
    assert status.liveness == "running"
    assert status.pid == 5353
    assert status.started_at == (first_start + timedelta(hours=1)).isoformat()
    assert status.cycle_count == 0


def test_daemon_status_persists_effective_poll_interval(tmp_path: Path) -> None:
    # Given: a daemon started with an effective CLI cadence.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        claimed = store.daemon.try_record_start(
            pid=4242,
            poll_interval=timedelta(minutes=60),
        )
        clock.advance(timedelta(minutes=16))

        # When: a consumer asks the store to derive liveness from that cadence.
        status = store.daemon.status()

    # Then: three effective poll intervals, not external config, set staleness.
    assert claimed is True
    assert status.poll_interval == timedelta(minutes=60)
    assert status.liveness == "running"


def test_heartbeat_without_a_started_daemon_is_rejected(tmp_path: Path) -> None:
    # Given: a database no daemon has started.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        # When/Then: a heartbeat cannot silently invent a running daemon.
        with pytest.raises(DaemonNotStartedError):
            store.daemon.record_heartbeat()
        assert store.daemon.status(stale_after=_STALE_AFTER).liveness == "never_started"


def test_second_live_claimant_does_not_overwrite_the_owner(tmp_path: Path) -> None:
    # Given: a live daemon that has completed one cycle.
    started = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(started)
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as owner,
        Store(database, clock=clock) as challenger,
    ):
        owner.daemon.record_start(pid=4242)
        clock.advance(timedelta(minutes=1))
        owner.daemon.record_heartbeat()
        before = owner.daemon.status(stale_after=_STALE_AFTER)

        # When: a second process claims the same singleton row.
        challenger.daemon.record_start(pid=7777)
        after = owner.daemon.status(stale_after=_STALE_AFTER)

    # Then: the incumbent keeps identity, start time, and cycle count.
    assert before.pid == 4242
    assert after.pid == 4242
    assert after.started_at == before.started_at
    assert after.heartbeat_at == before.heartbeat_at
    assert after.cycle_count == 1
    assert after.liveness == "running"


def test_dead_incumbent_can_be_reclaimed_with_explicit_process_probe(
    tmp_path: Path,
) -> None:
    # Given: a fresh running row whose process has crashed without recording stop.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as crashed,
        Store(database, clock=clock) as replacement,
    ):
        assert crashed.daemon.try_record_start(
            pid=4242,
            poll_interval=timedelta(minutes=5),
        )

        # When: the replacement proves the incumbent PID no longer exists.
        claimed = replacement.daemon.try_record_start(
            pid=5353,
            poll_interval=timedelta(minutes=5),
            incumbent_is_alive=lambda _pid: False,
        )
        status = replacement.daemon.status()

    # Then: crash recovery is immediate without weakening the default fence.
    assert claimed is True
    assert status.pid == 5353
    assert status.liveness == "running"


def test_non_owner_heartbeat_does_not_mutate_the_owner(tmp_path: Path) -> None:
    # Given: one process owns the liveness row.
    started = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(started)
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as owner,
        Store(database, clock=clock) as other,
    ):
        owner.daemon.record_start(pid=4242)
        before = owner.daemon.status(stale_after=_STALE_AFTER)

        # When: a process that does not own the row records a heartbeat.
        clock.advance(timedelta(minutes=1))
        other.daemon.record_heartbeat()
        after = owner.daemon.status(stale_after=_STALE_AFTER)

    # Then: cycle count and heartbeat stay with the owner.
    assert before.cycle_count == 0
    assert after.pid == 4242
    assert after.cycle_count == 0
    assert after.heartbeat_at == before.heartbeat_at
    assert after.started_at == before.started_at
    assert after.liveness == "running"


def test_non_owner_stop_does_not_mutate_the_owner(tmp_path: Path) -> None:
    # Given: one process owns the liveness row.
    started = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(started)
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as owner,
        Store(database, clock=clock) as other,
    ):
        owner.daemon.record_start(pid=4242)

        # When: a process that does not own the row records a stop.
        other.daemon.record_stop()
        status = owner.daemon.status(stale_after=_STALE_AFTER)

    # Then: the owner is still running under its own pid.
    assert status.liveness == "running"
    assert status.pid == 4242
    assert status.started_at == started.isoformat()
    assert status.cycle_count == 0


def test_non_owner_heartbeat_and_stop_do_not_mutate_a_stale_owner(
    tmp_path: Path,
) -> None:
    # Given: the incumbent is stale and still owns the row.
    started = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(started)
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as owner,
        Store(database, clock=clock) as other,
    ):
        owner.daemon.record_start(pid=4242)
        clock.advance(_STALE_AFTER + timedelta(seconds=1))
        before = owner.daemon.status(stale_after=_STALE_AFTER)

        # When: a non-owner heartbeats and then stops.
        other.daemon.record_heartbeat()
        other.daemon.record_stop()
        after = owner.daemon.status(stale_after=_STALE_AFTER)

    # Then: the stale owner's identity and heartbeat are untouched.
    assert before.liveness == "stale"
    assert after.liveness == "stale"
    assert after.pid == 4242
    assert after.cycle_count == 0
    assert after.started_at == before.started_at
    assert after.heartbeat_at == before.heartbeat_at


def test_displaced_owner_heartbeat_and_stop_do_not_mutate_the_successor(
    tmp_path: Path,
) -> None:
    # Given: a clean successor now owns the singleton row.
    first_start = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(first_start)
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as previous,
        Store(database, clock=clock) as successor,
    ):
        previous.daemon.record_start(pid=4242)
        previous.daemon.record_heartbeat()
        previous.daemon.record_stop()
        clock.advance(timedelta(hours=1))
        successor.daemon.record_start(pid=7777)
        before = successor.daemon.status(stale_after=_STALE_AFTER)

        # When: the displaced process heartbeats and then stops.
        clock.advance(timedelta(minutes=1))
        previous.daemon.record_heartbeat()
        previous.daemon.record_stop()
        after = successor.daemon.status(stale_after=_STALE_AFTER)

    # Then: the successor's running row is unchanged.
    assert before.pid == 7777
    assert before.liveness == "running"
    assert after.pid == 7777
    assert after.liveness == "running"
    assert after.cycle_count == 0
    assert after.started_at == before.started_at
    assert after.heartbeat_at == before.heartbeat_at
