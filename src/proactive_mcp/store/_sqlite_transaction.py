"""Atomic immediate transactions shared by SQLite stores."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


class ImmediateTransaction:
    """Serialize a write transaction and roll it back on failure."""

    _connection: sqlite3.Connection

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        _ = self._connection.execute("BEGIN IMMEDIATE")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._rollback_if_active()
            return
        try:
            _ = self._connection.execute("COMMIT")
        except sqlite3.Error:
            self._rollback_if_active()
            raise

    def _rollback_if_active(self) -> None:
        if self._connection.in_transaction:
            _ = self._connection.execute("ROLLBACK")
