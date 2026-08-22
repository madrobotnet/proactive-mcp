"""Daemon heartbeat persistence and liveness evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from ._daemon_models import (
    DAEMON_HEARTBEAT_ADAPTER,
    NEVER_STARTED,
    DaemonHeartbeat,
    DaemonLiveness,
    DaemonNotStartedError,
    DaemonStatus,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

__all__ = [
    "DaemonNotStartedError",
    "DaemonStatusStore",
    "InvalidDaemonPollIntervalError",
]

_STALE_POLL_COUNT: Final = 3
_INSERT_START: Final = """
            INSERT INTO daemon_status (
                id, state, pid, started_at, heartbeat_at, cycle_count,
                owner_token, poll_interval_seconds
            ) VALUES (1, 'running', ?, ?, ?, 0, ?, ?)
            """
_REPLACE_START: Final = """
            UPDATE daemon_status
            SET state = 'running', pid = ?, started_at = ?, heartbeat_at = ?,
                cycle_count = 0, owner_token = ?, poll_interval_seconds = ?
            WHERE id = 1
            """
_RECORD_HEARTBEAT: Final = """
            UPDATE daemon_status
            SET state = 'running', heartbeat_at = ?,
                cycle_count = cycle_count + 1
            WHERE id = 1 AND state = 'running' AND owner_token = ?
            """
_RECORD_STOP: Final = """
            UPDATE daemon_status
            SET state = 'stopped', heartbeat_at = ?
            WHERE id = 1 AND state = 'running' AND owner_token = ?
            """
_SELECT_HEARTBEAT: Final = """
            SELECT SUM(_proactive_capture_daemon_heartbeat(json_object(
                'state', state, 'pid', pid, 'started_at', started_at,
                'heartbeat_at', heartbeat_at, 'cycle_count', cycle_count,
                'owner_token', owner_token,
                'poll_interval_seconds', poll_interval_seconds
            )))
            FROM daemon_status WHERE id = 1
            """


class InvalidDaemonPollIntervalError(ValueError):
    """Raised when daemon cadence is zero or negative."""

    def __init__(self) -> None:
        """Initialize the stable boundary-safe message."""
        super().__init__("poll interval must be positive")


class DaemonStatusStore:
    """Own the single daemon liveness row and its staleness verdict.

    Every instance has an opaque owner token. The token, rather than a reusable
    PID, fences heartbeat and stop writes from previous or competing runs.
    """

    _connection: sqlite3.Connection
    _clock: Clock
    _heartbeats: list[DaemonHeartbeat]
    _owner_token: str

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind daemon liveness to an open connection and injected clock."""
        self._connection = connection
        self._clock = clock
        self._heartbeats = []
        self._owner_token = uuid4().hex
        connection.create_function(
            "_proactive_capture_daemon_heartbeat",
            1,
            self._capture_heartbeat,
        )

    def record_start(
        self,
        pid: int,
        *,
        poll_interval: timedelta | None = None,
    ) -> None:
        """Claim the daemon row without overwriting a live incumbent."""
        _ = self.try_record_start(pid, poll_interval=poll_interval)

    def try_record_start(
        self,
        pid: int,
        *,
        poll_interval: timedelta | None = None,
    ) -> bool:
        """Atomically claim the daemon row, returning whether this run owns it.

        A running incumbent is never overwritten while its persisted cadence
        says it is live. A stopped or expired run may be replaced. Repeating a
        start on the current owner is idempotent.
        """
        interval_seconds = self._interval_seconds(poll_interval)
        now = self._clock.now().astimezone(UTC)
        timestamp = now.isoformat()
        with ImmediateTransaction(self._connection):
            incumbent = self._heartbeat()
            if incumbent is None:
                _ = self._connection.execute(
                    _INSERT_START,
                    (
                        pid,
                        timestamp,
                        timestamp,
                        self._owner_token,
                        interval_seconds,
                    ),
                )
                return True
            if (
                incumbent.state == "running"
                and incumbent.owner_token == self._owner_token
            ):
                return True
            fallback_interval = (
                None
                if interval_seconds is None
                else timedelta(seconds=interval_seconds)
            )
            if incumbent.state == "running" and not self._expired(
                incumbent,
                now,
                fallback_interval=fallback_interval,
            ):
                return False
            _ = self._connection.execute(
                _REPLACE_START,
                (
                    pid,
                    timestamp,
                    timestamp,
                    self._owner_token,
                    interval_seconds,
                ),
            )
            return True

    def record_heartbeat(self) -> None:
        """Record a cycle only when this daemon instance owns the running row."""
        self._record_owned(_RECORD_HEARTBEAT)

    def record_stop(self) -> None:
        """Stop the row only when this daemon instance owns the running row."""
        self._record_owned(_RECORD_STOP)

    def status(self, *, stale_after: timedelta | None = None) -> DaemonStatus:
        """Return liveness and the effective persisted polling cadence.

        When ``stale_after`` is omitted, three persisted polling intervals are
        used. Callers may still supply a threshold for legacy rows that predate
        cadence persistence.
        """
        heartbeat = self._heartbeat()
        if heartbeat is None:
            return NEVER_STARTED
        poll_interval = self._poll_interval(heartbeat)
        threshold = stale_after
        if threshold is None:
            threshold = (
                _STALE_POLL_COUNT * poll_interval
                if poll_interval is not None
                else timedelta(0)
            )
        return DaemonStatus(
            liveness=self._liveness(heartbeat, threshold),
            pid=heartbeat.pid,
            started_at=heartbeat.started_at,
            heartbeat_at=heartbeat.heartbeat_at,
            cycle_count=heartbeat.cycle_count,
            poll_interval=poll_interval,
        )

    def _liveness(
        self,
        heartbeat: DaemonHeartbeat,
        stale_after: timedelta,
    ) -> DaemonLiveness:
        match heartbeat.state:
            case "stopped":
                return "stopped"
            case "running":
                elapsed = self._clock.now() - datetime.fromisoformat(
                    heartbeat.heartbeat_at
                )
                return "running" if elapsed <= stale_after else "stale"

    def _record_owned(self, sql: str) -> None:
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                sql,
                (self._now_iso(), self._owner_token),
            )
            if cursor.rowcount > 0:
                return
            if self._heartbeat() is None:
                raise DaemonNotStartedError

    def _expired(
        self,
        heartbeat: DaemonHeartbeat,
        now: datetime,
        *,
        fallback_interval: timedelta | None,
    ) -> bool:
        poll_interval = self._poll_interval(heartbeat) or fallback_interval
        if poll_interval is None:
            return False
        heartbeat_at = datetime.fromisoformat(heartbeat.heartbeat_at)
        return now - heartbeat_at > _STALE_POLL_COUNT * poll_interval

    def _heartbeat(self) -> DaemonHeartbeat | None:
        self._heartbeats.clear()
        _ = self._connection.execute(_SELECT_HEARTBEAT)
        return self._heartbeats[0] if self._heartbeats else None

    def _capture_heartbeat(self, payload: str) -> int:
        heartbeat = DAEMON_HEARTBEAT_ADAPTER.validate_json(payload)
        self._heartbeats.append(heartbeat)
        return heartbeat.pid

    def _now_iso(self) -> str:
        return self._clock.now().astimezone(UTC).isoformat()

    @staticmethod
    def _interval_seconds(poll_interval: timedelta | None) -> float | None:
        if poll_interval is None:
            return None
        seconds = poll_interval.total_seconds()
        if seconds <= 0:
            raise InvalidDaemonPollIntervalError
        return seconds

    @staticmethod
    def _poll_interval(heartbeat: DaemonHeartbeat) -> timedelta | None:
        seconds = heartbeat.poll_interval_seconds
        return None if seconds is None else timedelta(seconds=seconds)
