"""Daemon ownership and scheduling state-machine scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, NoReturn

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from proactive_mcp.delivery.daemon import (
    DaemonDependencies,
    DaemonFailureError,
    DaemonSchedule,
    WatcherDaemon,
)
from proactive_mcp.store import Store
from tests.daemon_test_support import (
    FakeEvaluationRunner,
    RecordingHeartbeat,
    RecordingNotifier,
    RecordingScheduler,
    local_only_pass,
)
from tests.situation_test_support import FakeClock, utc_datetime

_PID = 4242
_POLL_INTERVAL = timedelta(minutes=5)
_START = utc_datetime(2026, 8, 21, 12)
_RUN_METADATA_FAILURE = "injected run metadata failure"


def _daemon(
    heartbeat: RecordingHeartbeat,
    evaluation: FakeEvaluationRunner,
    notifier: RecordingNotifier,
) -> WatcherDaemon:
    return WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=evaluation.clock,
            heartbeat=heartbeat,
            evaluation=evaluation,
            notifier=notifier,
        )
    )


@dataclass(frozen=True, slots=True)
class _RaisingEvaluation:
    """Fail the evaluation pass the way a lost database or socket would."""

    error: Exception

    def run_once(self) -> NoReturn:
        raise self.error


@dataclass(frozen=True, slots=True)
class _FencedHeartbeat:
    """Refuse a second live claimant while still recording every call."""

    incumbent_pid: int
    events: list[str] = field(default_factory=list)

    def try_record_start(
        self, pid: int, *, poll_interval: timedelta | None = None
    ) -> bool:
        del poll_interval
        if pid != self.incumbent_pid:
            self.events.append(f"reject:{pid}")
            return False
        self.events.append(f"start:{pid}")
        return True

    def record_start(self, pid: int) -> None:
        _ = self.try_record_start(pid)

    def record_heartbeat(self) -> None:
        self.events.append("heartbeat")

    def record_stop(self) -> None:
        self.events.append("stop")


@dataclass(frozen=True, slots=True)
class _RunStartFailingHeartbeat:
    """Fail run metadata persistence after taking daemon ownership."""

    events: list[str] = field(default_factory=list)

    def try_record_start(
        self,
        pid: int,
        *,
        poll_interval: timedelta | None = None,
    ) -> bool:
        del poll_interval
        self.events.append(f"start:{pid}")
        return True

    def record_start(self, pid: int) -> None:
        _ = self.try_record_start(pid)

    def record_run_started(self, mode: str) -> None:
        self.events.append(f"run-start:{mode}")
        raise RuntimeError(_RUN_METADATA_FAILURE)

    def record_run_outcome(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("run-outcome")

    def record_heartbeat(self) -> None:
        self.events.append("heartbeat")

    def record_stop(self) -> None:
        self.events.append("stop")


def test_once_path_runs_exactly_one_pass_and_never_waits() -> None:
    # Given: a watcher whose collaborators record every call they receive.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(
        result=local_only_pass(),
        clock=clock,
        duration=timedelta(minutes=2),
    )
    heartbeat = RecordingHeartbeat()
    notifier = RecordingNotifier(situation_ids=(7,))

    # When: the library once-path runs.
    completed = _daemon(heartbeat, runner, notifier).run_once()

    # Then: exactly one pass ran and the process ended without a wait.
    assert len(runner.passes) == 1
    assert heartbeat.events == [f"start:{_PID}", "heartbeat", "stop"]
    assert len(completed.notifications) == 1
    assert notifier.dispatches == [_START + timedelta(minutes=2)]
    assert clock.now() == _START + timedelta(minutes=2)


def test_once_path_releases_owner_when_run_metadata_start_fails() -> None:
    clock = FakeClock(_START)
    heartbeat = _RunStartFailingHeartbeat()
    daemon = WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=clock,
            heartbeat=heartbeat,
            evaluation=FakeEvaluationRunner(
                result=local_only_pass(),
                clock=clock,
                duration=timedelta(),
            ),
            notifier=RecordingNotifier(),
        )
    )

    with pytest.raises(RuntimeError, match=_RUN_METADATA_FAILURE):
        _ = daemon.run_once()

    assert heartbeat.events == [f"start:{_PID}", "run-start:once", "stop"]


def test_once_path_stops_daemon_status_when_evaluation_raises() -> None:
    # Given: a watcher whose evaluation fails after start is recorded.
    clock = FakeClock(_START)
    heartbeat = RecordingHeartbeat()
    notifier = RecordingNotifier()
    daemon = WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=clock,
            heartbeat=heartbeat,
            evaluation=_RaisingEvaluation(RuntimeError("evaluation failed")),
            notifier=notifier,
        )
    )

    # When: the library once-path is interrupted by that evaluation error.
    with pytest.raises(DaemonFailureError) as raised:
        _ = daemon.run_once()

    # Then: the dead process must not keep the liveness row running.
    assert raised.value.args == ("evaluation", "failed")
    assert notifier.dispatches == []
    assert heartbeat.events == [f"start:{_PID}", "stop"]


def test_continuous_loop_waits_the_poll_interval_left_after_each_pass() -> None:
    # Given: five-minute polling and passes that each take two minutes.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(
        result=local_only_pass(),
        clock=clock,
        duration=timedelta(minutes=2),
    )
    scheduler = RecordingScheduler(stop_after=3)
    heartbeat = RecordingHeartbeat()

    # When: the loop runs until the scheduler stops it.
    run = _daemon(heartbeat, runner, RecordingNotifier()).run_forever(
        DaemonSchedule(scheduler=scheduler, poll_interval=_POLL_INTERVAL)
    )

    # Then: every wait is the cadence remainder, with one heartbeat per pass.
    assert run.pass_count == 3
    assert scheduler.waits == [timedelta(minutes=3)] * 3
    assert heartbeat.events == [
        f"start:{_PID}",
        "heartbeat",
        "heartbeat",
        "heartbeat",
        "stop",
    ]


def test_non_owner_once_path_does_not_record_stop() -> None:
    # Given: a live incumbent and a second watcher that fails its pass.
    heartbeat = _FencedHeartbeat(incumbent_pid=1111)
    notifier = RecordingNotifier()
    daemon = WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=FakeClock(_START),
            heartbeat=heartbeat,
            evaluation=_RaisingEvaluation(RuntimeError("evaluation failed")),
            notifier=notifier,
        )
    )

    # When: the once-path cannot claim runtime ownership.
    with pytest.raises(DaemonFailureError) as raised:
        _ = daemon.run_once()

    # Then: the challenger does no work and never mutates the incumbent.
    assert raised.value.args == ("runtime_ownership", "ownership_conflict")
    assert notifier.dispatches == []
    assert heartbeat.events == [f"reject:{_PID}"]


def test_non_owner_continuous_loop_does_not_record_stop() -> None:
    # Given: a live incumbent and a second watcher that completes one pass.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(result=local_only_pass(), clock=clock)
    heartbeat = _FencedHeartbeat(incumbent_pid=1111)

    # When: the continuous loop cannot claim runtime ownership.
    daemon = WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=clock,
            heartbeat=heartbeat,
            evaluation=runner,
            notifier=RecordingNotifier(),
        )
    )
    with pytest.raises(DaemonFailureError) as raised:
        _ = daemon.run_forever(
            DaemonSchedule(
                scheduler=RecordingScheduler(stop_after=1),
                poll_interval=_POLL_INTERVAL,
            )
        )

    # Then: no work or liveness mutation crosses the ownership fence.
    assert raised.value.args == ("runtime_ownership", "ownership_conflict")
    assert runner.passes == []
    assert heartbeat.events == [f"reject:{_PID}"]


def test_second_live_watcher_does_not_stop_the_owner(tmp_path: Path) -> None:
    # Given: a live owner that has already completed one cycle.
    clock = FakeClock(_START)
    database = tmp_path / "proactive.db"
    with (
        Store(database, clock=clock) as owner_store,
        Store(database, clock=clock) as other_store,
    ):
        claimed = owner_store.daemon.try_record_start(
            pid=_PID,
            poll_interval=_POLL_INTERVAL,
        )
        owner_store.daemon.record_heartbeat()
        challenger = WatcherDaemon(
            DaemonDependencies(
                pid=7777,
                clock=clock,
                heartbeat=other_store.daemon,
                evaluation=_RaisingEvaluation(RuntimeError("evaluation failed")),
                notifier=RecordingNotifier(),
            )
        )

        # When: a second watcher is rejected before its pass.
        with pytest.raises(DaemonFailureError) as raised:
            _ = challenger.run_once()
        status = owner_store.daemon.status(stale_after=_POLL_INTERVAL)

    # Then: the incumbent is still running under its own pid.
    assert raised.value.args == ("runtime_ownership", "ownership_conflict")
    assert claimed is True
    assert status.liveness == "running"
    assert status.pid == _PID
    assert status.cycle_count == 1
    assert status.started_at == _START.isoformat()
