"""Watcher daemon composition: heartbeat, evaluation, fallback notification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Literal,
    Protocol,
    TypeVar,
    final,
    runtime_checkable,
)

from proactive_mcp.delivery.evaluation import SkippedSources
from proactive_mcp.delivery.fallback import FallbackFailed
from proactive_mcp.scheduler import remaining_delay

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime, timedelta

    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery.evaluation import EvaluationPass
    from proactive_mcp.scheduler import Scheduler

__all__ = [
    "DaemonDependencies",
    "DaemonFailureCode",
    "DaemonFailureError",
    "DaemonFailureKind",
    "DaemonFailurePhase",
    "DaemonPass",
    "DaemonRun",
    "DaemonSchedule",
    "EvaluationRunner",
    "FallbackNotifier",
    "FallbackOutcome",
    "HeartbeatRecorder",
    "WatcherDaemon",
    "run_daemon_phase",
]


DaemonFailurePhase = Literal[
    "config", "database", "credential", "source_sync", "evaluation",
    "notification", "heartbeat", "runtime_ownership",
]
DaemonFailureCode = Literal[
    "invalid", "unsafe_path", "open_failed", "unavailable", "failed",
    "ownership_conflict",
]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _FailureIdentity:
    phase: DaemonFailurePhase
    code: DaemonFailureCode


class DaemonFailureKind(Enum):
    """Every stable daemon failure pair accepted at the process boundary."""

    CONFIG_INVALID = _FailureIdentity("config", "invalid")
    DATABASE_UNSAFE_PATH = _FailureIdentity("database", "unsafe_path")
    DATABASE_OPEN_FAILED = _FailureIdentity("database", "open_failed")
    CREDENTIAL_UNAVAILABLE = _FailureIdentity("credential", "unavailable")
    SOURCE_SYNC_FAILED = _FailureIdentity("source_sync", "failed")
    EVALUATION_FAILED = _FailureIdentity("evaluation", "failed")
    NOTIFICATION_FAILED = _FailureIdentity("notification", "failed")
    HEARTBEAT_FAILED = _FailureIdentity("heartbeat", "failed")
    OWNERSHIP_CONFLICT = _FailureIdentity("runtime_ownership", "ownership_conflict")


@final
class DaemonFailureError(Exception):
    """A phase and code with no underlying exception data."""

    __slots__: tuple[str, ...] = ("_kind",)
    _kind: DaemonFailureKind

    def __init__(self, kind: DaemonFailureKind) -> None:
        """Expose only bounded machine values through Exception.args."""
        self._kind = kind
        super().__init__(self.phase, self.code)

    @property
    def phase(self) -> DaemonFailurePhase:
        """Return the journal-safe failed phase."""
        return self._kind.value.phase

    @property
    def code(self) -> DaemonFailureCode:
        """Return the journal-safe reason code."""
        return self._kind.value.code


def run_daemon_phase(kind: DaemonFailureKind, operation: Callable[[], _T]) -> _T:
    """Normalize an exception at one named daemon decision boundary."""
    try:
        return operation()
    except DaemonFailureError:
        raise
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        raise DaemonFailureError(kind) from None


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


@runtime_checkable
class _ClaimableHeartbeat(Protocol):
    """A recorder that returns whether this run owns the singleton row."""

    def try_record_start(
        self,
        pid: int,
        *,
        poll_interval: timedelta | None = None,
    ) -> bool:
        """Claim the row and return whether this run is the owner."""
        ...


@dataclass(frozen=True, slots=True)
class _OwnerToken:
    """Proof that this watcher claimed the singleton liveness row."""

    pid: int


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
    """Compose heartbeat, evaluation, and fallback without agent delivery."""

    _dependencies: DaemonDependencies

    def __init__(self, dependencies: DaemonDependencies) -> None:
        """Bind this watcher to one process identity and its collaborators."""
        self._dependencies = dependencies

    def run_once(self) -> DaemonPass:
        """Claim liveness, run exactly one pass, and stop without waiting."""
        heartbeat = self._dependencies.heartbeat
        owner = self._claim(heartbeat)
        try:
            return self._run_pass(owner)
        finally:
            self._release(heartbeat, owner)

    def run_forever(self, schedule: DaemonSchedule) -> DaemonRun:
        """Run passes on a fixed cadence until the scheduler ends the loop."""
        clock = self._dependencies.clock
        heartbeat = self._dependencies.heartbeat
        owner = self._claim(heartbeat, poll_interval=schedule.poll_interval)
        passes = 0
        notifications = 0
        try:
            running = True
            while running:
                started = clock.now()
                completed = self._run_pass(owner)
                passes += 1
                notifications += len(completed.notifications)
                running = schedule.scheduler.wait(
                    remaining_delay(schedule.poll_interval, clock.now() - started)
                )
            return DaemonRun(pass_count=passes, notification_count=notifications)
        finally:
            self._release(heartbeat, owner)

    def _claim(
        self,
        heartbeat: HeartbeatRecorder,
        *,
        poll_interval: timedelta | None = None,
    ) -> _OwnerToken | None:
        """Return an owner token when this run claimed the singleton row."""
        pid = self._dependencies.pid
        if isinstance(heartbeat, _ClaimableHeartbeat):
            claimed = run_daemon_phase(
                DaemonFailureKind.HEARTBEAT_FAILED,
                lambda: heartbeat.try_record_start(
                    pid,
                    poll_interval=poll_interval,
                ),
            )
            if not claimed:
                raise DaemonFailureError(DaemonFailureKind.OWNERSHIP_CONFLICT)
            return _OwnerToken(pid)
        run_daemon_phase(
            DaemonFailureKind.HEARTBEAT_FAILED,
            lambda: heartbeat.record_start(pid),
        )
        return _OwnerToken(pid)

    def _run_pass(self, owner: _OwnerToken | None) -> DaemonPass:
        """Evaluate, notify, and heartbeat only when this run owns liveness."""
        evaluation = run_daemon_phase(
            DaemonFailureKind.EVALUATION_FAILED,
            self._dependencies.evaluation.run_once,
        )
        if evaluation.sources == SkippedSources("credential_storage_unavailable"):
            raise DaemonFailureError(DaemonFailureKind.CREDENTIAL_UNAVAILABLE)
        notifications = tuple(
            run_daemon_phase(
                DaemonFailureKind.NOTIFICATION_FAILED,
                lambda: self._dependencies.notifier.dispatch(
                    self._dependencies.clock.now()
                ),
            )
        )
        if any(isinstance(item, FallbackFailed) for item in notifications):
            raise DaemonFailureError(DaemonFailureKind.NOTIFICATION_FAILED)
        if owner is not None:
            run_daemon_phase(
                DaemonFailureKind.HEARTBEAT_FAILED,
                self._dependencies.heartbeat.record_heartbeat,
            )
        return DaemonPass(evaluation=evaluation, notifications=notifications)

    @staticmethod
    def _release(
        heartbeat: HeartbeatRecorder,
        owner: _OwnerToken | None,
    ) -> None:
        """Stop only the row this run owns."""
        if owner is not None:
            run_daemon_phase(
                DaemonFailureKind.HEARTBEAT_FAILED,
                heartbeat.record_stop,
            )
