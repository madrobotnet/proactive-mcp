"""Watcher daemon composition: heartbeat, evaluation, fallback notification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from proactive_mcp.scheduler import remaining_delay

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery.evaluation import EvaluationPass
    from proactive_mcp.scheduler import Scheduler

__all__ = [
    "DaemonDependencies",
    "DaemonPass",
    "DaemonRun",
    "DaemonSchedule",
    "EvaluationRunner",
    "FallbackNotifier",
    "FallbackOutcome",
    "HeartbeatRecorder",
    "WatcherDaemon",
]


class HeartbeatRecorder(Protocol):
    """Own the liveness record of one watcher daemon process."""

    def record_start(self, pid: int) -> None:
        """Claim the liveness record for this process."""
        ...

    def record_heartbeat(self) -> None:
        """Record one completed evaluation cycle."""
        ...

    def record_stop(self) -> None:
        """Record a clean shutdown of this process."""
        ...


class EvaluationRunner(Protocol):
    """Run the one evaluation pass shared with proactive_check."""

    def run_once(self) -> EvaluationPass:
        """Evaluate once and report the observable pass outcome."""
        ...


class FallbackOutcome(Protocol):
    """One situation whose single OS notification was attempted."""

    @property
    def situation_id(self) -> int:
        """Return the id of the notified situation."""
        ...


class FallbackNotifier(Protocol):
    """Raise OS notifications for situations no agent received in time."""

    def dispatch(self, now: datetime) -> Sequence[FallbackOutcome]:
        """Notify every situation whose configured wait elapsed by ``now``."""
        ...


@dataclass(frozen=True, slots=True)
class DaemonDependencies:
    """The identity and collaborators of one watcher daemon process."""

    pid: int
    clock: Clock
    heartbeat: HeartbeatRecorder
    evaluation: EvaluationRunner
    notifier: FallbackNotifier


@dataclass(frozen=True, slots=True)
class DaemonSchedule:
    """The cadence controls of the continuous watcher loop."""

    scheduler: Scheduler
    poll_interval: timedelta


@dataclass(frozen=True, slots=True)
class DaemonPass:
    """One completed watcher pass and the notifications it raised."""

    evaluation: EvaluationPass
    notifications: tuple[FallbackOutcome, ...]


@dataclass(frozen=True, slots=True)
class DaemonRun:
    """The observable totals of one continuous watcher run."""

    pass_count: int
    notification_count: int


class WatcherDaemon:
    """Compose one watcher process from its liveness, evaluation, and fallback.

    The daemon never claims delivery of a situation: only an agent calling
    ``proactive_check`` may mark one delivered (§5.1). A pass therefore
    evaluates, hands unreceived rows to the OS notification fallback, and
    records the heartbeat that proves the cycle completed.
    """

    _dependencies: DaemonDependencies

    def __init__(self, dependencies: DaemonDependencies) -> None:
        """Bind this watcher to one process identity and its collaborators."""
        self._dependencies = dependencies

    def run_once(self) -> DaemonPass:
        """Claim liveness, run exactly one pass, and stop without waiting."""
        heartbeat = self._dependencies.heartbeat
        heartbeat.record_start(self._dependencies.pid)
        completed = self._run_pass()
        heartbeat.record_stop()
        return completed

    def run_forever(self, schedule: DaemonSchedule) -> DaemonRun:
        """Run passes on a fixed cadence until the scheduler ends the loop."""
        clock = self._dependencies.clock
        heartbeat = self._dependencies.heartbeat
        heartbeat.record_start(self._dependencies.pid)
        passes = 0
        notifications = 0
        running = True
        while running:
            started = clock.now()
            completed = self._run_pass()
            passes += 1
            notifications += len(completed.notifications)
            running = schedule.scheduler.wait(
                remaining_delay(schedule.poll_interval, clock.now() - started)
            )
        heartbeat.record_stop()
        return DaemonRun(pass_count=passes, notification_count=notifications)

    def _run_pass(self) -> DaemonPass:
        evaluation = self._dependencies.evaluation.run_once()
        notifications = self._dependencies.notifier.dispatch(
            self._dependencies.clock.now()
        )
        self._dependencies.heartbeat.record_heartbeat()
        return DaemonPass(evaluation=evaluation, notifications=tuple(notifications))
