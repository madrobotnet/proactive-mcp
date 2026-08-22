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


def test_heartbeat_without_a_started_daemon_is_rejected(tmp_path: Path) -> None:
    # Given: a database no daemon has started.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    with Store(tmp_path / "db", clock=clock) as store:
        # When/Then: a heartbeat cannot silently invent a running daemon.
        with pytest.raises(DaemonNotStartedError):
            store.daemon.record_heartbeat()
        assert store.daemon.status(stale_after=_STALE_AFTER).liveness == "never_started"
