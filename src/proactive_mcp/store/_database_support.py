"""Typed status, errors, and scalar reads for the SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    """Observed SQLite configuration after migrations have been applied."""

    path: Path
    journal_mode: str
    busy_timeout: int
    migration_version: int


@dataclass(frozen=True, slots=True)
class InvalidBusyTimeoutError(Exception):
    """Raised when busy_timeout_ms is negative."""

    value: int


@dataclass(frozen=True, slots=True)
class StoreClosedError(Exception):
    """Raised when the store is used after close()."""


@dataclass(frozen=True, slots=True)
class StoreQueryError(Exception):
    """Raised when a status PRAGMA or version query returns no value."""

    query: str


class ScalarReader:
    """Read scalars via UDFs because sqlite3 fetchone is typed as Any."""

    _connection: sqlite3.Connection
    _ints: list[int]
    _strs: list[str]

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._ints = []
        self._strs = []
        connection.create_function("_proactive_capture_int", 1, self._capture_int)
        connection.create_function("_proactive_capture_str", 1, self._capture_str)

    def query_int(
        self,
        select_sql: str,
        params: tuple[str, ...] = (),
    ) -> int:
        """Return one integer scalar."""
        self._ints.clear()
        _ = self._connection.execute(
            f"SELECT _proactive_capture_int(({select_sql}))",
            params,
        )
        if not self._ints:
            raise StoreQueryError(select_sql)
        return self._ints[0]

    def query_str(self, select_sql: str) -> str:
        """Return one string scalar."""
        self._strs.clear()
        _ = self._connection.execute(f"SELECT _proactive_capture_str(({select_sql}))")
        if not self._strs:
            raise StoreQueryError(select_sql)
        return self._strs[0]

    def _capture_int(self, value: int) -> int:
        self._ints.append(value)
        return value

    def _capture_str(self, value: str) -> str:
        self._strs.append(value)
        return value
