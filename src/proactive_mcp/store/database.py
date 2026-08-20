"""SQLite store with WAL mode, busy_timeout, and idempotent migrations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from .migrations import load_migrations

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5000


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


class _ScalarReader:
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
        self._ints.clear()
        _ = self._connection.execute(
            f"SELECT _proactive_capture_int(({select_sql}))",
            params,
        )
        if not self._ints:
            raise StoreQueryError(select_sql)
        return self._ints[0]

    def query_str(self, select_sql: str) -> str:
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


class Store:
    """Owns a SQLite connection. Mutation is required to open and close it."""

    _path: Path
    _busy_timeout_ms: int
    _connection: sqlite3.Connection | None
    _reader: _ScalarReader | None

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        """Open the database and apply pending migrations."""
        if busy_timeout_ms < 0:
            raise InvalidBusyTimeoutError(busy_timeout_ms)
        self._path = path.expanduser().resolve()
        self._busy_timeout_ms = busy_timeout_ms
        self._connection = None
        self._reader = None
        try:
            self._open()
        except sqlite3.Error:
            self.close()
            raise

    def __enter__(self) -> Self:
        """Return this open store."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close the store when leaving its context."""
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection if it is open."""
        connection = self._connection
        self._connection = None
        self._reader = None
        if connection is not None:
            connection.close()

    def status(self) -> DatabaseStatus:
        """Return path, journal mode, busy timeout, and migration version."""
        reader = self._require_reader()
        return DatabaseStatus(
            path=self._path,
            journal_mode=reader.query_str(
                "SELECT journal_mode FROM pragma_journal_mode"
            ),
            busy_timeout=reader.query_int("SELECT timeout FROM pragma_busy_timeout"),
            migration_version=_current_version(reader),
        )

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._path,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.isolation_level = None
        _ = connection.execute("PRAGMA journal_mode = WAL")
        _ = connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms:d}")
        reader = _ScalarReader(connection)
        _apply_migrations(connection, reader)
        self._connection = connection
        self._reader = reader

    def _require_reader(self) -> _ScalarReader:
        reader = self._reader
        if reader is None:
            raise StoreClosedError
        return reader


def _apply_migrations(
    connection: sqlite3.Connection,
    reader: _ScalarReader,
) -> None:
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        current_version = _current_version(reader)
        for version, sql in load_migrations():
            if version <= current_version:
                continue
            for statement in _sql_statements(sql):
                _ = connection.execute(statement)
            _ = connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            current_version = version
        _ = connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            _ = connection.execute("ROLLBACK")
        raise


def _current_version(reader: _ScalarReader) -> int:
    table_count = reader.query_int(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
        ("table", "schema_migrations"),
    )
    if table_count == 0:
        return 0
    return reader.query_int("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")


def _sql_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    for part in sql.split(";"):
        statement = part.strip()
        if statement:
            statements.append(statement)
    return tuple(statements)
