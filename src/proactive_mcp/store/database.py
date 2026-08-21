"""SQLite store with WAL mode, busy_timeout, and idempotent migrations."""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING, Final, Self

from proactive_mcp.clock import Clock, UtcClock

from ._database_support import (
    DatabaseStatus,
    InvalidBusyTimeoutError,
    ScalarReader,
    StoreClosedError,
)
from ._memory_normalize import normalize_alias, normalize_label
from .memory import (
    Entity,
    EntityKind,
    MemoryItem,
    MemoryKind,
    MemoryStore,
    NewMemory,
)
from .migrate import apply_migrations, current_version
from .private_path import (
    UnsafeDatabasePathError,
    enforce_private_sidecars,
    open_private_parent,
    prepare_private_database_file,
    private_initialization_lock,
    sqlite_connection_target,
)
from .situations import SituationStore
from .sync import (
    SourceAuthState,
    SourceName,
    SourceSyncFailureCode,
    SourceSyncState,
    SyncStore,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5000
__all__ = ["DEFAULT_BUSY_TIMEOUT_MS", "DatabaseStatus", "Store"]


class Store:
    """Owns a SQLite connection. Mutation is required to open and close it."""

    _path: Path
    _busy_timeout_ms: int
    _clock: Clock
    _connection: sqlite3.Connection | None
    _reader: ScalarReader | None
    _memory_store: MemoryStore | None
    _sync_store: SyncStore | None
    _situation_store: SituationStore | None
    _directory_fd: int | None

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        clock: Clock | None = None,
    ) -> None:
        """Open the database and apply pending migrations."""
        if busy_timeout_ms < 0:
            raise InvalidBusyTimeoutError(busy_timeout_ms)
        self._path = path.expanduser().absolute()
        self._busy_timeout_ms = busy_timeout_ms
        self._clock = clock if clock is not None else UtcClock()
        self._connection = None
        self._reader = None
        self._memory_store = None
        self._sync_store = None
        self._situation_store = None
        self._directory_fd = None
        try:
            self._open()
        except (OSError, sqlite3.Error, UnsafeDatabasePathError):
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
        directory_fd = self._directory_fd
        self._connection = None
        self._reader = None
        self._memory_store = None
        self._sync_store = None
        self._situation_store = None
        self._directory_fd = None
        if connection is not None:
            connection.close()
        if directory_fd is not None:
            os.close(directory_fd)

    def status(self) -> DatabaseStatus:
        """Return path, journal mode, busy timeout, and migration version."""
        reader = self._require_reader()
        return DatabaseStatus(
            path=self._path,
            journal_mode=reader.query_str(
                "SELECT journal_mode FROM pragma_journal_mode"
            ),
            busy_timeout=reader.query_int("SELECT timeout FROM pragma_busy_timeout"),
            migration_version=current_version(reader),
        )

    def connection(self) -> sqlite3.Connection:
        """Return the live SQLite connection for storage-layer integrations."""
        connection = self._connection
        if connection is None:
            raise StoreClosedError
        return connection

    def remember(self, memory: NewMemory) -> MemoryItem:
        """Store a memory item without replacing contradictory items."""
        return self._require_memory_store().remember(memory)

    def recall(
        self,
        query: str,
        *,
        kind: MemoryKind | None = None,
        entity_kind: EntityKind | None = None,
        path_prefix: str | None = None,
        limit: int = 20,
    ) -> tuple[MemoryItem, ...]:
        """Return active literal matches, newest first, across memory kinds."""
        return self._require_memory_store().recall(
            query,
            kind=kind,
            entity_kind=entity_kind,
            path_prefix=path_prefix,
            limit=limit,
        )

    def update(self, memory_id: int, memory: NewMemory) -> MemoryItem:
        """Replace a memory item's mutable values while retaining its identity."""
        return self._require_memory_store().update(memory_id, memory)

    def list_entities(
        self,
        *,
        kind: EntityKind | None = None,
        path_prefix: str | None = None,
    ) -> tuple[Entity, ...]:
        """List active entities with optional kind and path-prefix filters."""
        return self._require_memory_store().list_entities(
            kind=kind,
            path_prefix=path_prefix,
        )

    def forget(self, memory_id: int) -> MemoryItem:
        """Soft-archive an existing memory item."""
        return self._require_memory_store().forget(memory_id)

    def list_dated_memories(self) -> tuple[MemoryItem, ...]:
        """Return every active memory item that carries a date anchor."""
        return self._require_memory_store().list_dated_memories()

    @property
    def situations(self) -> SituationStore:
        """Return the situation persistence and state machine operations."""
        situation_store = self._situation_store
        if situation_store is None:
            raise StoreClosedError
        return situation_store

    def get_source_sync(self, source: SourceName) -> SourceSyncState:
        """Return persisted synchronization state for one Google source."""
        return self._require_sync_store().get_source_sync(source)

    def list_source_sync(self) -> tuple[SourceSyncState, SourceSyncState]:
        """Return Gmail and Calendar synchronization states in a stable order."""
        return self._require_sync_store().list_source_sync()

    def set_source_auth(self, source: SourceName, auth_state: SourceAuthState) -> None:
        """Persist the authorization state for one Google source."""
        self._require_sync_store().set_source_auth(source, auth_state)

    def set_google_auth_state(self, auth_state: SourceAuthState) -> None:
        """Persist the shared Google authorization state for both sources."""
        self._require_sync_store().set_google_auth_state(auth_state)

    def record_sync_success(
        self,
        source: SourceName,
        *,
        sync_cursor: str | None = None,
    ) -> None:
        """Record a successful source synchronization attempt."""
        self._require_sync_store().record_sync_success(source, sync_cursor=sync_cursor)

    def record_sync_failure(
        self,
        source: SourceName,
        *,
        error_code: SourceSyncFailureCode,
    ) -> None:
        """Record a normalized source synchronization failure."""
        self._require_sync_store().record_sync_failure(source, error_code=error_code)

    def record_google_invalid_grant(self) -> None:
        """Atomically mark both Google sources as requiring reauthorization."""
        self._require_sync_store().record_google_invalid_grant()

    def _open(self) -> None:
        directory_fd = open_private_parent(self._path)
        self._directory_fd = directory_fd
        prepare_private_database_file(directory_fd, self._path)
        with private_initialization_lock(directory_fd, self._path):
            connection = sqlite3.connect(
                sqlite_connection_target(directory_fd, self._path),
                timeout=self._busy_timeout_ms / 1000,
            )
            self._connection = connection
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys = ON").close()
            connection.execute(
                f"PRAGMA busy_timeout = {self._busy_timeout_ms:d}"
            ).close()
            connection.execute("PRAGMA journal_mode = WAL").close()
            reader = ScalarReader(connection)
            connection.create_function(
                "_proactive_alias_norm",
                1,
                normalize_alias,
                deterministic=True,
            )
            connection.create_function(
                "_proactive_normalize_label",
                1,
                normalize_label,
                deterministic=True,
            )
            apply_migrations(connection, reader)
            enforce_private_sidecars(directory_fd, self._path)
        self._reader = reader
        self._memory_store = MemoryStore(connection, self._clock)
        self._sync_store = SyncStore(connection, self._clock)
        self._situation_store = SituationStore(connection, self._clock)

    def _require_reader(self) -> ScalarReader:
        reader = self._reader
        if reader is None:
            raise StoreClosedError
        return reader

    def _require_memory_store(self) -> MemoryStore:
        memory_store = self._memory_store
        if memory_store is None:
            raise StoreClosedError
        return memory_store

    def _require_sync_store(self) -> SyncStore:
        sync_store = self._sync_store
        if sync_store is None:
            raise StoreClosedError
        return sync_store
