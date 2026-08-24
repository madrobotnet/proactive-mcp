"""SQLite store with WAL mode, busy_timeout, and idempotent migrations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self

from proactive_mcp.clock import Clock, UtcClock

from ._database_collaborators import (
    StoreCollaborators,
    close_connection,
    open_collaborators,
)
from ._database_support import (
    DatabaseStatus,
    InvalidBusyTimeoutError,
    StoreClosedError,
)
from .migrate import current_version

if TYPE_CHECKING:
    import sqlite3
    from datetime import timedelta
    from pathlib import Path
    from types import TracebackType

    from ._lazy_sync_lease import LazySyncLease
    from ._source_generation import SourceGeneration, SourceGenerationState
    from .daemon_status import DaemonStatusStore
    from .fallbacks import FallbackStore
    from .memory import (
        Entity,
        EntityKind,
        MemoryItem,
        MemoryKind,
        NewMemory,
    )
    from .situations import SituationStore
    from .sync import (
        SourceAuthState,
        SourceName,
        SourceSyncFailureCode,
        SourceSyncState,
    )

DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5000
__all__ = ["DEFAULT_BUSY_TIMEOUT_MS", "DatabaseStatus", "Store"]


class Store:
    """Owns a SQLite connection. Mutation is required to open and close it."""

    _path: Path
    _clock: Clock
    _collaborators: StoreCollaborators | None

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
        self._clock = clock if clock is not None else UtcClock()
        self._collaborators = None
        self._collaborators = open_collaborators(
            self._path,
            busy_timeout_ms,
            self._clock,
        )

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
        collaborators = self._collaborators
        self._collaborators = None
        if collaborators is not None:
            close_connection(
                collaborators.connection,
                collaborators.directory_fd,
                collaborators.database_guard,
            )

    def status(self) -> DatabaseStatus:
        """Return path, journal mode, busy timeout, and migration version."""
        reader = self._require().reader
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
        return self._require().connection

    def try_start_evaluation(self, *, minimum_interval: timedelta) -> bool:
        """Reserve one expensive evaluation inside a cross-process interval."""
        return self._require().evaluation_gate.try_start(
            minimum_interval=minimum_interval
        )

    def remember(self, memory: NewMemory) -> MemoryItem:
        """Store a memory item without replacing contradictory items."""
        return self._require().memory.remember(memory)

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
        return self._require().memory.recall(
            query,
            kind=kind,
            entity_kind=entity_kind,
            path_prefix=path_prefix,
            limit=limit,
        )

    def update(self, memory_id: int, memory: NewMemory) -> MemoryItem:
        """Replace a memory item's mutable values while retaining its identity."""
        return self._require().memory.update(memory_id, memory)

    def list_entities(
        self,
        *,
        kind: EntityKind | None = None,
        path_prefix: str | None = None,
        after_id: int = 0,
        limit: int = 20,
    ) -> tuple[Entity, ...]:
        """List active entities with optional kind and path-prefix filters."""
        return self._require().memory.list_entities(
            kind=kind,
            path_prefix=path_prefix,
            after_id=after_id,
            limit=limit,
        )

    def forget(self, memory_id: int) -> MemoryItem:
        """Soft-archive an existing memory item."""
        return self._require().memory.forget(memory_id)

    def list_dated_memories(self) -> tuple[MemoryItem, ...]:
        """Return every active memory item that carries a date anchor."""
        return self._require().memory.list_dated_memories()

    @property
    def situations(self) -> SituationStore:
        """Return the situation persistence and state machine operations."""
        return self._require().situations

    @property
    def daemon(self) -> DaemonStatusStore:
        """Return the daemon heartbeat and liveness operations."""
        return self._require().daemon

    @property
    def fallbacks(self) -> FallbackStore:
        """Return the one-shot OS notification fallback operations."""
        return self._require().fallbacks

    def acquire_lazy_sync_lease(
        self,
        *,
        lease_duration: timedelta,
    ) -> LazySyncLease | None:
        """Atomically reserve one degraded remote read until release or expiry."""
        return self._require().sync.acquire_lazy_sync_lease(
            lease_duration=lease_duration
        )

    def release_lazy_sync_lease(self, lease: LazySyncLease) -> bool:
        """Release a degraded-read reservation if this lease still owns it."""
        return self._require().sync.release_lazy_sync_lease(lease)

    def reserve_source_generation(self, source: SourceName) -> SourceGeneration:
        """Atomically issue the next detector generation for one source."""
        return self._require().sync.reserve_source_generation(source)

    def source_generation_state(self, source: SourceName) -> SourceGenerationState:
        """Return issued and accepted detector generation progress."""
        return self._require().sync.source_generation_state(source)

    def get_source_sync(self, source: SourceName) -> SourceSyncState:
        """Return persisted synchronization state for one Google source."""
        return self._require().sync.get_source_sync(source)

    def list_source_sync(self) -> tuple[SourceSyncState, SourceSyncState]:
        """Return Gmail and Calendar synchronization states in a stable order."""
        return self._require().sync.list_source_sync()

    def set_source_auth(self, source: SourceName, auth_state: SourceAuthState) -> None:
        """Persist the authorization state for one Google source."""
        self._require().sync.set_source_auth(source, auth_state)

    def set_google_auth_state(self, auth_state: SourceAuthState) -> None:
        """Persist the shared Google authorization state for both sources."""
        self._require().sync.set_google_auth_state(auth_state)

    def record_sync_success(
        self,
        source: SourceName,
        *,
        sync_cursor: str | None = None,
    ) -> None:
        """Record a successful source synchronization attempt."""
        self._require().sync.record_sync_success(source, sync_cursor=sync_cursor)

    def record_sync_failure(
        self,
        source: SourceName,
        *,
        error_code: SourceSyncFailureCode,
    ) -> None:
        """Record a normalized source synchronization failure."""
        self._require().sync.record_sync_failure(source, error_code=error_code)

    def record_google_invalid_grant(self) -> None:
        """Atomically mark both Google sources as requiring reauthorization."""
        self._require().sync.record_google_invalid_grant()

    def _require(self) -> StoreCollaborators:
        collaborators = self._collaborators
        if collaborators is None:
            raise StoreClosedError
        return collaborators
