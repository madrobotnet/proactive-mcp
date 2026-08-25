"""Typed persistence and freshness evaluation for Google source synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import (
    datetime,  # noqa: TC003 - Pydantic resolves this annotation at runtime.
)
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from pydantic import TypeAdapter

from ._lazy_sync_lease import LazySyncLease, LazySyncLeaseStore
from ._source_generation import (
    SourceGeneration,
    SourceGenerationState,
    SourceGenerationStatus,
    SourceGenerationStore,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3
    from datetime import timedelta

    from proactive_mcp.clock import Clock

SourceName = Literal["gmail", "calendar"]
SourceAuthState = Literal["not_configured", "configured", "needs_reauth"]
SourceErrorCode = Literal[
    "invalid_grant",
    "scope_mismatch",
    "http_4xx",
    "http_5xx",
    "network",
    "resource_limit",
    "timeout",
    "degraded",
    "unknown",
]
SourceSyncFailureCode = Literal[
    "scope_mismatch",
    "http_4xx",
    "http_5xx",
    "network",
    "resource_limit",
    "timeout",
    "degraded",
    "unknown",
]
SourceReadOutcome: TypeAlias = Literal[
    "healthy",
    "partial",
    "stale",
    "auth_error",
    "transport_error",
]
SourceReadReason: TypeAlias = Literal[
    "body_snippet_fallback",
    "body_truncated",
    "degraded",
    "direction_metadata_ambiguous",
    "direction_metadata_missing",
    "http_4xx",
    "http_5xx",
    "identity_headers_ambiguous",
    "invalid_grant",
    "mime_structure_truncated",
    "network",
    "never_synced",
    "not_configured",
    "pagination_limit",
    "resource_limit",
    "scope_mismatch",
    "stale",
    "sync_budget_exhausted",
    "thread_list_entry_skipped",
    "thread_projection_limit",
    "thread_response_too_large",
    "thread_without_projectable_message",
    "timeout",
    "unknown",
]
GMAIL_READ_BYTE_BUDGET: Final[int] = 8_000_000
_DIAGNOSTIC_COUNT_MAXIMA: Final[tuple[int, ...]] = (
    221,
    20,
    200,
    2_000,
    GMAIL_READ_BYTE_BUDGET,
)
_MAX_DIAGNOSTIC_REASON_COUNT: Final[int] = 200
_SOURCE_ERROR_OUTCOMES: Final[dict[SourceErrorCode, SourceReadOutcome]] = {
    "invalid_grant": "auth_error",
    "scope_mismatch": "auth_error",
    "http_4xx": "auth_error",
    "resource_limit": "partial",
    "degraded": "partial",
    "http_5xx": "transport_error",
    "network": "transport_error",
    "timeout": "transport_error",
    "unknown": "transport_error",
}
_GOOGLE_SOURCES: Final[tuple[SourceName, SourceName]] = ("gmail", "calendar")
_SOURCE_READ_OUTCOMES: Final = frozenset(
    {"healthy", "partial", "stale", "auth_error", "transport_error"}
)
_SOURCE_READ_REASONS: Final = frozenset(
    {
        "body_snippet_fallback",
        "body_truncated",
        "degraded",
        "direction_metadata_ambiguous",
        "direction_metadata_missing",
        "http_4xx",
        "http_5xx",
        "identity_headers_ambiguous",
        "invalid_grant",
        "mime_structure_truncated",
        "network",
        "never_synced",
        "not_configured",
        "pagination_limit",
        "resource_limit",
        "scope_mismatch",
        "stale",
        "sync_budget_exhausted",
        "thread_list_entry_skipped",
        "thread_projection_limit",
        "thread_response_too_large",
        "thread_without_projectable_message",
        "timeout",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class SourceReadReasonCount:
    """One closed diagnostic reason and its occurrence count."""

    reason: SourceReadReason
    count: int


@dataclass(frozen=True, slots=True)
class SourceReadDiagnostics:
    """PII-free counters and outcome for one Gmail read projection."""

    outcome: SourceReadOutcome
    request_count: int
    page_count: int
    projected_count: int
    excluded_count: int
    byte_budget: int
    reason_counts: tuple[SourceReadReasonCount, ...] = ()


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


@dataclass(frozen=True, slots=True)
class SourceHealthSnapshot:
    """Google source states and accepted Gmail diagnostics from one read."""

    gmail: SourceSyncState
    calendar: SourceSyncState
    gmail_diagnostics: SourceReadDiagnostics | None


_SOURCE_SYNC_STATE_ADAPTER: Final[TypeAdapter[SourceSyncState]] = TypeAdapter(
    SourceSyncState
)
_SOURCE_READ_DIAGNOSTICS_ADAPTER: Final[TypeAdapter[SourceReadDiagnostics]] = (
    TypeAdapter(SourceReadDiagnostics)
)


class InvalidSourceReadDiagnosticsError(ValueError):
    """Raised when diagnostics contain values outside the closed store contract."""

    def __init__(self) -> None:
        """Initialize a PII-free boundary error."""
        super().__init__("invalid Gmail diagnostics")


class SyncStore:
    """Persist source synchronization transitions through an open SQLite connection."""

    _connection: sqlite3.Connection
    _clock: Clock
    _states: list[SourceSyncState]
    _diagnostics: list[SourceReadDiagnostics]
    _generations: SourceGenerationStore
    _lazy_sync_leases: LazySyncLeaseStore

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind source synchronization operations to a connection and clock."""
        self._connection = connection
        self._clock = clock
        self._states = []
        self._diagnostics = []
        self._generations = SourceGenerationStore(connection)
        self._lazy_sync_leases = LazySyncLeaseStore(connection, clock)
        connection.create_function(
            "_proactive_capture_source_sync_state",
            1,
            self._capture_state,
        )
        connection.create_function(
            "_proactive_capture_gmail_diagnostics",
            1,
            self._capture_diagnostics,
        )

    def acquire_lazy_sync_lease(
        self,
        *,
        lease_duration: timedelta,
    ) -> LazySyncLease | None:
        """Atomically reserve one degraded remote read until release or expiry."""
        return self._lazy_sync_leases.acquire(lease_duration=lease_duration)

    def release_lazy_sync_lease(self, lease: LazySyncLease) -> bool:
        """Release a degraded-read reservation only when its token still owns it."""
        return self._lazy_sync_leases.release(lease)

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
        diagnostics: SourceReadDiagnostics | None = None,
    ) -> None:
        """Accept a generation and its diagnostics inside the caller transaction."""
        if diagnostics is not None:
            if generation.source != "gmail":
                raise InvalidSourceReadDiagnosticsError
            _validate_diagnostics(diagnostics)
        self._generations.accept(generation, status)
        if diagnostics is not None:
            self._write_gmail_diagnostics(diagnostics)

    def source_health_snapshot(self) -> SourceHealthSnapshot:
        """Return source state and diagnostics from one SQLite read snapshot."""
        self._states.clear()
        self._diagnostics.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(captured) FROM (
                SELECT
                    CASE source WHEN 'gmail' THEN 0 ELSE 1 END AS ordering,
                    _proactive_capture_source_sync_state(json_object(
                        'source', source,
                        'auth_state', auth_state,
                        'last_success_at', last_success_at,
                        'last_attempt_at', last_attempt_at,
                        'last_error_code', last_error_code,
                        'sync_cursor', sync_cursor,
                        'updated_at', updated_at
                    )) AS captured
                FROM source_sync_state
                UNION ALL
                SELECT 2, _proactive_capture_gmail_diagnostics(json_object(
                    'outcome', outcome,
                    'request_count', request_count,
                    'page_count', page_count,
                    'projected_count', projected_count,
                    'excluded_count', excluded_count,
                    'byte_budget', byte_budget,
                    'reason_counts', json(COALESCE((
                        SELECT json_group_array(json_object(
                            'reason', reason,
                            'count', count
                        ))
                        FROM (
                            SELECT reason, count
                            FROM gmail_diagnostic_reason_counts
                            WHERE diagnostic_id = gmail_diagnostics.id
                            ORDER BY reason
                        )
                    ), '[]'))
                ))
                FROM gmail_diagnostics WHERE id = 1
                ORDER BY ordering
            )
            """
        )
        persisted = {state.source: state for state in self._states}
        return SourceHealthSnapshot(
            gmail=persisted.get("gmail", _unconfigured_state("gmail")),
            calendar=persisted.get("calendar", _unconfigured_state("calendar")),
            gmail_diagnostics=None if not self._diagnostics else self._diagnostics[0],
        )

    def gmail_diagnostics(self) -> SourceReadDiagnostics | None:
        """Return the latest accepted Gmail diagnostics, when present."""
        self._diagnostics.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(_proactive_capture_gmail_diagnostics(json_object(
                'outcome', outcome,
                'request_count', request_count,
                'page_count', page_count,
                'projected_count', projected_count,
                'excluded_count', excluded_count,
                'byte_budget', byte_budget,
                'reason_counts', json(COALESCE((
                    SELECT json_group_array(json_object(
                        'reason', reason,
                        'count', count
                    ))
                    FROM (
                        SELECT reason, count
                        FROM gmail_diagnostic_reason_counts
                        WHERE diagnostic_id = gmail_diagnostics.id
                        ORDER BY reason
                    )
                ), '[]'))
            )))
            FROM gmail_diagnostics WHERE id = 1
            """
        )
        return None if not self._diagnostics else self._diagnostics[0]

    def record_gmail_sync(
        self,
        diagnostics: SourceReadDiagnostics,
        *,
        sync_cursor: str | None = None,
        error_code: SourceErrorCode | None = None,
    ) -> None:
        """Atomically persist one direct Gmail attempt and bounded diagnostics."""
        _validate_diagnostics(diagnostics)
        with ImmediateTransaction(self._connection):
            if error_code is None:
                self.record_sync_success("gmail", sync_cursor=sync_cursor)
            elif error_code == "invalid_grant":
                self._write_google_auth_state("needs_reauth", "invalid_grant")
            else:
                self.record_sync_failure("gmail", error_code=error_code)
            self._write_gmail_diagnostics(diagnostics)

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

    def record_google_invalid_grant(
        self,
        diagnostics: SourceReadDiagnostics | None = None,
    ) -> None:
        """Atomically require reauthorization and persist Gmail diagnostics."""
        if diagnostics is not None:
            _validate_diagnostics(diagnostics)
        with ImmediateTransaction(self._connection):
            self._write_google_auth_state("needs_reauth", "invalid_grant")
            if diagnostics is not None:
                self._write_gmail_diagnostics(diagnostics)

    def record_google_invalid_grant_in_transaction(self) -> None:
        """Require reauthorization inside an existing SQLite transaction."""
        self._write_google_auth_state("needs_reauth", "invalid_grant")

    def _update_google_auth_state(
        self,
        auth_state: SourceAuthState,
        error_code: SourceErrorCode | None,
    ) -> None:
        with ImmediateTransaction(self._connection):
            self._write_google_auth_state(auth_state, error_code)

    def _write_google_auth_state(
        self,
        auth_state: SourceAuthState,
        error_code: SourceErrorCode | None,
    ) -> None:
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

    def _write_gmail_diagnostics(
        self,
        diagnostics: SourceReadDiagnostics,
    ) -> None:
        _ = self._connection.execute(
            "DELETE FROM gmail_diagnostic_reason_counts WHERE diagnostic_id = 1"
        )
        _ = self._connection.execute(
            """
            INSERT INTO gmail_diagnostics (
                id, outcome, request_count, page_count, projected_count,
                excluded_count, byte_budget
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                outcome = excluded.outcome,
                request_count = excluded.request_count,
                page_count = excluded.page_count,
                projected_count = excluded.projected_count,
                excluded_count = excluded.excluded_count,
                byte_budget = excluded.byte_budget
            """,
            (
                diagnostics.outcome,
                diagnostics.request_count,
                diagnostics.page_count,
                diagnostics.projected_count,
                diagnostics.excluded_count,
                diagnostics.byte_budget,
            ),
        )
        for item in diagnostics.reason_counts:
            _ = self._connection.execute(
                """
                INSERT INTO gmail_diagnostic_reason_counts (
                    diagnostic_id, reason, count
                ) VALUES (1, ?, ?)
                """,
                (item.reason, item.count),
            )

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

    def _capture_diagnostics(self, payload: str) -> int:
        diagnostics = _SOURCE_READ_DIAGNOSTICS_ADAPTER.validate_json(payload)
        self._diagnostics.append(diagnostics)
        return 1


def _validate_diagnostics(diagnostics: SourceReadDiagnostics) -> None:
    counts = (
        diagnostics.request_count,
        diagnostics.page_count,
        diagnostics.projected_count,
        diagnostics.excluded_count,
        diagnostics.byte_budget,
    )
    reasons = tuple(item.reason for item in diagnostics.reason_counts)
    if (
        diagnostics.outcome not in _SOURCE_READ_OUTCOMES
        or any(
            type(value) is not int or not 0 <= value <= maximum
            for value, maximum in zip(counts, _DIAGNOSTIC_COUNT_MAXIMA, strict=True)
        )
        or len(reasons) != len(set(reasons))
        or any(
            item.reason not in _SOURCE_READ_REASONS
            or type(item.count) is not int
            or not 0 <= item.count <= _MAX_DIAGNOSTIC_REASON_COUNT
            for item in diagnostics.reason_counts
        )
    ):
        raise InvalidSourceReadDiagnosticsError


def source_failure_diagnostics(
    error_code: SourceErrorCode,
) -> SourceReadDiagnostics:
    """Map one normalized failure to its closed source outcome."""
    return SourceReadDiagnostics(
        outcome=_SOURCE_ERROR_OUTCOMES[error_code],
        request_count=0,
        page_count=0,
        projected_count=0,
        excluded_count=0,
        byte_budget=GMAIL_READ_BYTE_BUDGET,
        reason_counts=(SourceReadReasonCount(error_code, 1),),
    )


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
