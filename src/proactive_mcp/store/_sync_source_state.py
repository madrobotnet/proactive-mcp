"""Persistence transitions for Google source synchronization state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

    from ._sync_models import (
        SourceAuthState,
        SourceCredentialState,
        SourceErrorCode,
        SourceName,
        SourceSyncFailureCode,
    )

_GOOGLE_SOURCES: Final[tuple[SourceName, SourceName]] = ("gmail", "calendar")


class SourceStateStore:
    """Persist source freshness and shared Google authorization transitions."""

    _connection: sqlite3.Connection
    _clock: Clock

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._connection = connection
        self._clock = clock

    def set_auth(self, source: SourceName, auth_state: SourceAuthState) -> None:
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

    def record_success(
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

    def record_failure(
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

    def write_google_auth(
        self,
        auth_state: SourceAuthState,
        error_code: SourceErrorCode | None,
    ) -> None:
        """Write both Google source rows inside the caller transaction."""
        timestamp = self._clock.now().isoformat()
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

    def record_credential_state(self, state: SourceCredentialState) -> None:
        """Persist bounded credential availability for status coalescing."""
        _ = self._connection.execute(
            """
            INSERT INTO source_operational_state(
                id, credential_state, observed_at
            ) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                credential_state = excluded.credential_state,
                observed_at = excluded.observed_at
            """,
            (state, self._clock.now().isoformat()),
        )
