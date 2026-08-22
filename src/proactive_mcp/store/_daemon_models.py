"""Typed daemon heartbeat rows and derived liveness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import TypeAdapter

DaemonState = Literal["running", "stopped"]
DaemonLiveness = Literal["never_started", "running", "stale", "stopped"]


@dataclass(frozen=True, slots=True)
class DaemonHeartbeat:
    """The persisted liveness row of the most recent daemon process."""

    state: DaemonState
    pid: int
    started_at: str
    heartbeat_at: str
    cycle_count: int


@dataclass(frozen=True, slots=True)
class DaemonStatus:
    """Daemon liveness as reported by the status surface."""

    liveness: DaemonLiveness
    pid: int | None
    started_at: str | None
    heartbeat_at: str | None
    cycle_count: int


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
