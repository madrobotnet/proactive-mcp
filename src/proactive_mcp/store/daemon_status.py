"""Daemon heartbeat persistence and liveness evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

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
    from datetime import timedelta

    from proactive_mcp.clock import Clock

__all__ = ["DaemonNotStartedError", "DaemonStatusStore"]

_RECORD_START: Final = """
            INSERT INTO daemon_status (
                id, state, pid, started_at, heartbeat_at, cycle_count
            ) VALUES (1, 'running', ?, ?, ?, 0)
            ON CONFLICT(id) DO UPDATE SET
                state = 'running', pid = excluded.pid,
                started_at = excluded.started_at,
                heartbeat_at = excluded.heartbeat_at, cycle_count = 0
            """
_RECORD_HEARTBEAT: Final = """
            UPDATE daemon_status
            SET state = 'running', heartbeat_at = ?,
                cycle_count = cycle_count + 1
            WHERE id = 1
            """
_RECORD_STOP: Final = """
            UPDATE daemon_status
            SET state = 'stopped', heartbeat_at = ?
            WHERE id = 1
            """
_SELECT_HEARTBEAT: Final = """
            SELECT SUM(_proactive_capture_daemon_heartbeat(json_object(
                'state', state, 'pid', pid, 'started_at', started_at,
                'heartbeat_at', heartbeat_at, 'cycle_count', cycle_count
            )))
            FROM daemon_status WHERE id = 1
            """


class DaemonStatusStore:
    """Own the single daemon liveness row and its staleness verdict.

    The capture buffer is mutable because SQLite scalar callbacks cannot
    return structured Python records directly.
    """

    _connection: sqlite3.Connection
    _clock: Clock
    _heartbeats: list[DaemonHeartbeat]

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind daemon liveness to an open connection and injected clock."""
        self._connection = connection
        self._clock = clock
        self._heartbeats = []
        connection.create_function(
            "_proactive_capture_daemon_heartbeat",
            1,
            self._capture_heartbeat,
        )

    def record_start(self, pid: int) -> None:
        """Claim the liveness row for one daemon process at zero cycles."""
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            _ = self._connection.execute(_RECORD_START, (pid, timestamp, timestamp))

    def record_heartbeat(self) -> None:
        """Record one completed evaluation cycle of the running daemon."""
        self._require_started(_RECORD_HEARTBEAT)

    def record_stop(self) -> None:
        """Record a clean shutdown of the running daemon."""
        self._require_started(_RECORD_STOP)

    def status(self, *, stale_after: timedelta) -> DaemonStatus:
        """Return liveness for one heartbeat staleness threshold."""
        heartbeat = self._heartbeat()
        if heartbeat is None:
            return NEVER_STARTED
        return DaemonStatus(
            liveness=self._liveness(heartbeat, stale_after),
            pid=heartbeat.pid,
            started_at=heartbeat.started_at,
            heartbeat_at=heartbeat.heartbeat_at,
            cycle_count=heartbeat.cycle_count,
        )

    def _liveness(
        self,
        heartbeat: DaemonHeartbeat,
        stale_after: timedelta,
    ) -> DaemonLiveness:
        # Exhaustive over DaemonState: adding a state breaks this match at
        # type-check time rather than silently reporting a live daemon.
        match heartbeat.state:
            case "stopped":
                return "stopped"
            case "running":
                elapsed = self._clock.now() - datetime.fromisoformat(
                    heartbeat.heartbeat_at
                )
                return "running" if elapsed <= stale_after else "stale"

    def _require_started(self, sql: str) -> None:
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(sql, (self._now_iso(),))
            if cursor.rowcount == 0:
                raise DaemonNotStartedError

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
