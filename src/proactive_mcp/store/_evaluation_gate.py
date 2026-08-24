"""Cross-process minimum interval for expensive local evaluation passes."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3
    from datetime import timedelta

    from proactive_mcp.clock import Clock


class EvaluationGate:
    """Coalesce rapid or concurrent proactive evaluation attempts in SQLite."""

    _connection: sqlite3.Connection
    _clock: Clock

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._connection = connection
        self._clock = clock

    def try_start(self, *, minimum_interval: timedelta) -> bool:
        """Reserve the current interval once across all server processes."""
        now = self._clock.now().astimezone(UTC)
        cutoff = now - minimum_interval
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                """
                INSERT INTO evaluation_gate(id, last_started_at) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET last_started_at = excluded.last_started_at
                WHERE evaluation_gate.last_started_at <= ?
                """,
                (now.isoformat(), cutoff.isoformat()),
            )
        return cursor.rowcount == 1
