"""Typed persistence and freshness evaluation for Google source synchronization."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import (
    datetime,  # noqa: TC003 - Pydantic resolves this annotation at runtime.
)
from typing import TYPE_CHECKING, Final, Literal

from pydantic import TypeAdapter

from ._source_generation import (
    SourceGeneration,
    SourceGenerationState,
    SourceGenerationStatus,
    SourceGenerationStore,
)

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock

SourceName = Literal["gmail", "calendar"]
SourceAuthState = Literal["not_configured", "configured", "needs_reauth"]
SourceErrorCode = Literal[
    "invalid_grant",
    "scope_mismatch",
    "http_4xx",
    "http_5xx",
    "network",
    "timeout",
    "unknown",
]
SourceSyncFailureCode = Literal[
    "scope_mismatch",
    "http_4xx",
    "http_5xx",
    "network",
    "timeout",
    "unknown",
]
_GOOGLE_SOURCES: Final[tuple[SourceName, SourceName]] = ("gmail", "calendar")


@dataclass(frozen=True, slots=True)
class SourceSyncState:
    """The persisted synchronization state for one Google source."""

    source: SourceName
    auth_state: SourceAuthState
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: SourceErrorCode | None
    sync_cursor: str | None
    updated_at: datetime | None


_SOURCE_SYNC_STATE_ADAPTER: Final[TypeAdapter[SourceSyncState]] = TypeAdapter(
    SourceSyncState
)


class SyncStore:
    """Persist source synchronization transitions through an open SQLite connection."""

    _connection: sqlite3.Connection
    _clock: Clock
    _states: list[SourceSyncState]
    _generations: SourceGenerationStore

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind source synchronization operations to a connection and clock."""
        self._connection = connection
        self._clock = clock
        self._states = []
        self._generations = SourceGenerationStore(connection)
        connection.create_function(
            "_proactive_capture_source_sync_state",
            1,
            self._capture_state,
        )

    def reserve_source_generation(self, source: SourceName) -> SourceGeneration:
        """Atomically issue the next generation for one source."""
        return self._generations.reserve(source)

    def source_generation_state(self, source: SourceName) -> SourceGenerationState:
        """Return generation progress for one source."""
        return self._generations.state(source)

    def accept_source_generation(
        self,
        generation: SourceGeneration,
        status: SourceGenerationStatus,
    ) -> None:
        """Accept a generation inside the caller's transaction."""
        self._generations.accept(generation, status)

    def get_source_sync(self, source: SourceName) -> SourceSyncState:
        """Return persisted state, or the explicit never-configured source state."""
        gmail, calendar = self.list_source_sync()
        return {"gmail": gmail, "calendar": calendar}[source]

    def list_source_sync(self) -> tuple[SourceSyncState, SourceSyncState]:
        """Return Gmail and Calendar synchronization states in a stable order."""
        persisted = {state.source: state for state in self._persisted_states()}
        return (
            persisted.get("gmail", _unconfigured_state("gmail")),
            persisted.get("calendar", _unconfigured_state("calendar")),
        )

    def set_source_auth(self, source: SourceName, auth_state: SourceAuthState) -> None:
        """Persist the authorization state for one source."""
        timestamp = self._clock.now().isoformat()
        _ = self._connection.execute(
            """
            INSERT INTO source_sync_state (source, auth_state, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                auth_state = excluded.auth_state,
                updated_at = excluded.updated_at
            """,
            (source, auth_state, timestamp),
        )

    def set_google_auth_state(self, auth_state: SourceAuthState) -> None:
        """Persist one shared Google authorization state for both sources."""
        self._update_google_auth_state(auth_state, None)

    def record_sync_success(
        self,
        source: SourceName,
        *,
        sync_cursor: str | None = None,
    ) -> None:
        """Record a successful synchronization attempt for one source."""
        timestamp = self._clock.now().isoformat()
        _ = self._connection.execute(
            """
            INSERT INTO source_sync_state (
                source, auth_state, last_success_at, last_attempt_at, sync_cursor,
                updated_at
            ) VALUES (?, 'configured', ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_success_at = excluded.last_success_at,
                last_attempt_at = excluded.last_attempt_at,
                last_error_code = NULL,
                sync_cursor = excluded.sync_cursor,
                updated_at = excluded.updated_at
            """,
            (source, timestamp, timestamp, sync_cursor, timestamp),
        )

    def record_sync_failure(
        self,
        source: SourceName,
        *,
        error_code: SourceSyncFailureCode,
    ) -> None:
        """Record a normalized, non-authorization failure for one source."""
        timestamp = self._clock.now().isoformat()
        _ = self._connection.execute(
            """
            INSERT INTO source_sync_state (
                source, auth_state, last_attempt_at, last_error_code, updated_at
            ) VALUES (?, 'configured', ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_error_code = excluded.last_error_code,
                updated_at = excluded.updated_at
            """,
            (source, timestamp, error_code, timestamp),
        )

    def record_google_invalid_grant(self) -> None:
        """Atomically require reauthorization for the shared Google grant."""
        self._update_google_auth_state("needs_reauth", "invalid_grant")

    def _update_google_auth_state(
        self,
        auth_state: SourceAuthState,
        error_code: SourceErrorCode | None,
    ) -> None:
        timestamp = self._clock.now().isoformat()
        _ = self._connection.execute("BEGIN IMMEDIATE")
        try:
            for source in _GOOGLE_SOURCES:
                _ = self._connection.execute(
                    """
                    INSERT INTO source_sync_state (
                        source, auth_state, last_attempt_at, last_error_code, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        auth_state = excluded.auth_state,
                        last_attempt_at = CASE
                            WHEN excluded.last_error_code IS NULL
                            THEN source_sync_state.last_attempt_at
                            ELSE excluded.last_attempt_at
                        END,
                        last_error_code = CASE
                            WHEN excluded.last_error_code IS NULL
                            THEN source_sync_state.last_error_code
                            ELSE excluded.last_error_code
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source,
                        auth_state,
                        timestamp if error_code is not None else None,
                        error_code,
                        timestamp,
                    ),
                )
            _ = self._connection.execute("COMMIT")
        except sqlite3.Error:
            if self._connection.in_transaction:
                _ = self._connection.execute("ROLLBACK")
            raise

    def _persisted_states(self) -> tuple[SourceSyncState, ...]:
        self._states.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(_proactive_capture_source_sync_state(
                json_object(
                    'source', source,
                    'auth_state', auth_state,
                    'last_success_at', last_success_at,
                    'last_attempt_at', last_attempt_at,
                    'last_error_code', last_error_code,
                    'sync_cursor', sync_cursor,
                    'updated_at', updated_at
                )
            ))
            FROM (
                SELECT * FROM source_sync_state
                ORDER BY CASE source WHEN 'gmail' THEN 0 ELSE 1 END
            )
            """
        )
        return tuple(self._states)

    def _capture_state(self, payload: str) -> int:
        state = _SOURCE_SYNC_STATE_ADAPTER.validate_json(payload)
        self._states.append(state)
        return 1


def _unconfigured_state(source: SourceName) -> SourceSyncState:
    return SourceSyncState(
        source=source,
        auth_state="not_configured",
        last_success_at=None,
        last_attempt_at=None,
        last_error_code=None,
        sync_cursor=None,
        updated_at=None,
    )
