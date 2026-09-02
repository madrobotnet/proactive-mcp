"""Typed daemon heartbeat rows and derived liveness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from datetime import timedelta

from pydantic import TypeAdapter

DaemonState = Literal["running", "stopped"]
DaemonLiveness = Literal["never_started", "running", "stale", "stopped"]
DaemonRunMode = Literal["once", "continuous"]
DaemonLastRunState = Literal["never_run", "unknown", "succeeded", "degraded", "failed"]
DaemonFailurePhase = Literal[
    "config",
    "database",
    "credential",
    "source_sync",
    "evaluation",
    "notification",
    "heartbeat",
    "runtime_ownership",
    "service",
]
DaemonFailureCode = Literal[
    "invalid",
    "unsafe_path",
    "open_failed",
    "unavailable",
    "failed",
    "ownership_conflict",
    "notify_failed",
]


@dataclass(frozen=True, slots=True)
class DaemonHeartbeat:
    """The persisted liveness row of the most recent daemon process."""

    state: DaemonState
    pid: int
    started_at: str
    heartbeat_at: str
    cycle_count: int
    owner_token: str | None
    poll_interval_seconds: float | None
    mode: DaemonRunMode | None
    last_run_state: DaemonLastRunState
    last_failure_phase: DaemonFailurePhase | None
    last_failure_code: DaemonFailureCode | None
    last_failure_at: str | None
    last_completed_at: str | None


@dataclass(frozen=True, slots=True)
class DaemonStatus:
    """Daemon liveness as reported by the status surface."""

    liveness: DaemonLiveness
    pid: int | None
    started_at: str | None
    heartbeat_at: str | None
    cycle_count: int
    poll_interval: timedelta | None = None
    mode: DaemonRunMode | None = None
    last_run_state: DaemonLastRunState = "never_run"
    last_failure_phase: DaemonFailurePhase | None = None
    last_failure_code: DaemonFailureCode | None = None
    last_failure_at: str | None = None
    last_completed_at: str | None = None


NEVER_STARTED: Final[DaemonStatus] = DaemonStatus(
    liveness="never_started",
    pid=None,
    started_at=None,
    heartbeat_at=None,
    cycle_count=0,
)
DAEMON_HEARTBEAT_ADAPTER: Final[TypeAdapter[DaemonHeartbeat]] = TypeAdapter(
    DaemonHeartbeat
)


@dataclass(frozen=True, slots=True)
class DaemonNotStartedError(Exception):
    """Raised when a heartbeat or stop arrives without a started daemon."""

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, "no daemon process has recorded a start")
