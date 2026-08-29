"""Typed persistence and freshness evaluation for Google source synchronization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._lazy_sync_lease import LazySyncLease, LazySyncLeaseStore
from ._source_generation import (
    SourceGeneration,
    SourceGenerationState,
    SourceGenerationStatus,
    SourceGenerationStore,
)
from ._sqlite_transaction import ImmediateTransaction
from ._sync_diagnostics import GmailDiagnosticsStore
from ._sync_models import (
    GMAIL_READ_BYTE_BUDGET,
    InvalidSourceReadDiagnosticsError,
    SourceAuthState,
    SourceCredentialState,
    SourceErrorCode,
    SourceHealthSnapshot,
    SourceName,
    SourceReadDiagnostics,
    SourceReadOutcome,
    SourceReadReason,
    SourceReadReasonCount,
    SourceSyncFailureCode,
    SourceSyncState,
    source_failure_diagnostics,
    unconfigured_state,
)
from ._sync_snapshot import SourceSnapshotStore
from ._sync_source_state import SourceStateStore

if TYPE_CHECKING:
    import sqlite3
    from datetime import timedelta

    from proactive_mcp.clock import Clock

SourceReadReasonCount.__module__ = __name__
SourceReadDiagnostics.__module__ = __name__
SourceSyncState.__module__ = __name__
SourceHealthSnapshot.__module__ = __name__
InvalidSourceReadDiagnosticsError.__module__ = __name__
source_failure_diagnostics.__module__ = __name__

__all__ = [
    "GMAIL_READ_BYTE_BUDGET",
    "InvalidSourceReadDiagnosticsError",
    "SourceAuthState",
    "SourceCredentialState",
    "SourceErrorCode",
    "SourceHealthSnapshot",
    "SourceName",
    "SourceReadDiagnostics",
    "SourceReadOutcome",
    "SourceReadReason",
    "SourceReadReasonCount",
    "SourceSyncFailureCode",
    "SourceSyncState",
    "SyncStore",
    "source_failure_diagnostics",
]


class SyncStore:
    """Orchestrate source synchronization through one open SQLite connection."""

    _connection: sqlite3.Connection
    _states: SourceStateStore
    _snapshots: SourceSnapshotStore
    _diagnostics: GmailDiagnosticsStore
    _generations: SourceGenerationStore
    _lazy_sync_leases: LazySyncLeaseStore

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind source synchronization operations to a connection and clock."""
        self._connection = connection
        self._states = SourceStateStore(connection, clock)
        self._snapshots = SourceSnapshotStore(connection)
        self._diagnostics = GmailDiagnosticsStore(connection)
        self._generations = SourceGenerationStore(connection, clock)
        self._lazy_sync_leases = LazySyncLeaseStore(connection, clock)

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
            self._diagnostics.validate(diagnostics)
        self._generations.accept(generation, status)
        if diagnostics is not None:
            self._diagnostics.write(diagnostics)

    def source_health_snapshot(self) -> SourceHealthSnapshot:
        """Return source state and diagnostics from one SQLite read snapshot."""
        return self._snapshots.health()

    def gmail_diagnostics(self) -> SourceReadDiagnostics | None:
        """Return the latest accepted Gmail diagnostics, when present."""
        return self._snapshots.gmail_diagnostics()

    def record_gmail_sync(
        self,
        diagnostics: SourceReadDiagnostics,
        *,
        sync_cursor: str | None = None,
        error_code: SourceErrorCode | None = None,
    ) -> None:
        """Atomically persist one direct Gmail attempt and bounded diagnostics."""
        self._diagnostics.validate(diagnostics)
        with ImmediateTransaction(self._connection):
            if error_code is None:
                self.record_sync_success("gmail", sync_cursor=sync_cursor)
            elif error_code == "invalid_grant":
                self._states.write_google_auth("needs_reauth", "invalid_grant")
            else:
                self.record_sync_failure("gmail", error_code=error_code)
            self._diagnostics.write(diagnostics)

    def get_source_sync(self, source: SourceName) -> SourceSyncState:
        """Return persisted state, or the explicit never-configured source state."""
        gmail, calendar = self.list_source_sync()
        return {"gmail": gmail, "calendar": calendar}[source]

    def list_source_sync(self) -> tuple[SourceSyncState, SourceSyncState]:
        """Return Gmail and Calendar synchronization states in a stable order."""
        persisted = {state.source: state for state in self._snapshots.states()}
        return (
            persisted.get("gmail", unconfigured_state("gmail")),
            persisted.get("calendar", unconfigured_state("calendar")),
        )

    def set_source_auth(self, source: SourceName, auth_state: SourceAuthState) -> None:
        """Persist the authorization state for one source."""
        self._states.set_auth(source, auth_state)

    def set_google_auth_state(self, auth_state: SourceAuthState) -> None:
        """Persist one shared Google authorization state for both sources."""
        with ImmediateTransaction(self._connection):
            self._states.write_google_auth(auth_state, None)

    def record_credential_state(self, state: SourceCredentialState) -> None:
        """Persist bounded credential availability without credential data."""
        self._states.record_credential_state(state)

    def record_sync_success(
        self,
        source: SourceName,
        *,
        sync_cursor: str | None = None,
    ) -> None:
        """Record a successful synchronization attempt for one source."""
        self._states.record_success(source, sync_cursor=sync_cursor)

    def record_sync_failure(
        self,
        source: SourceName,
        *,
        error_code: SourceSyncFailureCode,
    ) -> None:
        """Record a normalized, non-authorization failure for one source."""
        self._states.record_failure(source, error_code=error_code)

    def record_google_invalid_grant(
        self,
        diagnostics: SourceReadDiagnostics | None = None,
    ) -> None:
        """Atomically require reauthorization and persist Gmail diagnostics."""
        if diagnostics is not None:
            self._diagnostics.validate(diagnostics)
        with ImmediateTransaction(self._connection):
            self._states.write_google_auth("needs_reauth", "invalid_grant")
            if diagnostics is not None:
                self._diagnostics.write(diagnostics)

    def record_google_invalid_grant_in_transaction(self) -> None:
        """Require reauthorization inside an existing SQLite transaction."""
        self._states.write_google_auth("needs_reauth", "invalid_grant")
