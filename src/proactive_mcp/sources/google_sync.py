"""Read-only Gmail and Calendar synchronization orchestration."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from typing_extensions import override

from proactive_mcp.situations.inputs import EngineInputs, SourceSnapshot
from proactive_mcp.sources.calendar import CalendarError
from proactive_mcp.sources.credentials import CredentialStorageError
from proactive_mcp.sources.gmail import GmailError

if TYPE_CHECKING:
    from proactive_mcp.sources.calendar import CalendarReadResult
    from proactive_mcp.sources.gmail import GmailInboxReadResult
    from proactive_mcp.store import (
        SourceErrorCode,
        SourceGeneration,
        SourceSyncFailureCode,
        Store,
    )


@dataclass(frozen=True, slots=True)
class InvalidGrantError(Exception):
    """Signal that the shared Google OAuth grant must be renewed."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe operator message."""
        return "Google authorization is no longer valid"


GoogleTransportErrorCode: TypeAlias = Literal["network", "timeout", "unknown"]


@dataclass(frozen=True, slots=True)
class GoogleTransportError(Exception):
    """A credential-safe network failure from the authenticated transport."""

    error_code: GoogleTransportErrorCode

    @override
    def __str__(self) -> str:
        """Return a provider-detail-free transport message."""
        return "Google read transport failed"


@dataclass(frozen=True, slots=True)
class GoogleReadSmokeDisabledError(Exception):
    """Signal that a real-account read was not explicitly enabled."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe operator message."""
        return "Google read smoke requires explicit opt-in"


class GoogleCredentialStore(Protocol):
    """Delete the shared OAuth credential after grant revocation."""

    def delete(self) -> None:
        """Delete any persisted credential material."""
        ...


class GmailProfileReader(Protocol):
    """Read detector-ready Gmail inbox threads without persisting success."""

    def read_inbox_threads(self) -> GmailInboxReadResult:
        """Return the authenticated inbox projection."""
        ...


class CalendarEventsReader(Protocol):
    """Read primary Calendar events without M3 evaluation."""

    def list_events(self) -> CalendarReadResult:
        """Return the primary Calendar events."""
        ...


@dataclass(frozen=True, slots=True)
class GoogleReadDependencies:
    """Authenticated read adapters and persistence required for one Google sync."""

    store: Store
    gmail: GmailProfileReader
    calendar: CalendarEventsReader
    credentials: GoogleCredentialStore


@dataclass(frozen=True, slots=True)
class GoogleReadSummary:
    """PII-free observable outcome of one Google read operation."""

    gmail_count: int
    gmail_ids: tuple[str, ...]
    gmail_error_code: SourceErrorCode | None
    calendar_count: int
    calendar_ids: tuple[str, ...]
    calendar_error_code: SourceErrorCode | None
    credential_cleanup_failed: bool = False


@dataclass(frozen=True, slots=True)
class _SourceReadOutcome:
    """Internal redacted outcome for one source read."""

    count: int
    ids: tuple[str, ...]
    error_code: SourceSyncFailureCode | None


class GoogleSyncService:
    """Persist independent read-only Gmail and Calendar freshness transitions."""

    _dependencies: GoogleReadDependencies

    def __init__(self, dependencies: GoogleReadDependencies) -> None:
        """Bind authenticated readers, credential storage, and the sync store."""
        self._dependencies = dependencies

    def sync(self) -> GoogleReadSummary:
        """Read both sources, persisting each result without M3 evaluation."""
        try:
            gmail = self._sync_gmail()
            calendar = self._sync_calendar()
        except InvalidGrantError:
            return self._reauthentication_summary()
        return _summary(gmail, calendar)

    def read_smoke(self, *, enabled: bool) -> GoogleReadSummary:
        """Perform the real-account read only when the caller explicitly opts in."""
        if not enabled:
            raise GoogleReadSmokeDisabledError
        return self.sync()

    def prepare_evaluation(self) -> EngineInputs:
        """Read ordered source snapshots without accepting their truth yet."""
        store = self._dependencies.store
        gmail_generation = store.reserve_source_generation("gmail")
        calendar_generation = store.reserve_source_generation("calendar")
        try:
            gmail_result = self._dependencies.gmail.read_inbox_threads()
        except InvalidGrantError:
            return self._invalid_grant_inputs(gmail_generation, calendar_generation)
        except (GmailError, GoogleTransportError) as error:
            gmail_snapshot = SourceSnapshot(
                generation=gmail_generation,
                items=(),
                complete=False,
                warning_codes=(f"gmail_{error.error_code}",),
                error_code=error.error_code,
            )
        else:
            gmail_snapshot = SourceSnapshot(
                generation=gmail_generation,
                items=gmail_result.threads,
                complete=gmail_result.is_complete,
                sync_cursor=gmail_result.provider_history_cursor,
                warning_codes=tuple(gmail_result.degradation_reasons),
                resolve_absent=gmail_result.allows_absent_resolution,
                resolution_scope_ids=gmail_result.resolution_safe_thread_ids,
                resolution_excluded_ids=(gmail_result.resolution_excluded_thread_ids),
            )
        try:
            calendar_result = self._dependencies.calendar.list_events()
        except InvalidGrantError:
            return self._invalid_grant_inputs(gmail_generation, calendar_generation)
        except (CalendarError, GoogleTransportError) as error:
            calendar_snapshot = SourceSnapshot(
                generation=calendar_generation,
                items=(),
                complete=False,
                warning_codes=(f"calendar_{error.error_code}",),
                error_code=error.error_code,
            )
        else:
            calendar_snapshot = SourceSnapshot(
                generation=calendar_generation,
                items=calendar_result.events,
                complete=calendar_result.skipped_count == 0,
                warning_codes=(
                    ()
                    if calendar_result.skipped_count == 0
                    else ("calendar_skipped_items",)
                ),
            )
        return EngineInputs(
            gmail_threads=gmail_snapshot,
            calendar_events=calendar_snapshot,
        )

    def _invalid_grant_inputs(
        self,
        gmail_generation: SourceGeneration,
        calendar_generation: SourceGeneration,
    ) -> EngineInputs:
        with suppress(CredentialStorageError):
            self._dependencies.credentials.delete()
        return EngineInputs(
            gmail_threads=SourceSnapshot(
                generation=gmail_generation,
                items=(),
                complete=False,
                warning_codes=("gmail_invalid_grant",),
                error_code="invalid_grant",
            ),
            calendar_events=SourceSnapshot(
                generation=calendar_generation,
                items=(),
                complete=False,
                warning_codes=("calendar_invalid_grant",),
                error_code="invalid_grant",
            ),
        )

    def _sync_gmail(self) -> _SourceReadOutcome:
        try:
            result = self._dependencies.gmail.read_inbox_threads()
        except (GmailError, GoogleTransportError) as error:
            return self._record_failure("gmail", error.error_code)
        if result.is_complete:
            self._dependencies.store.record_sync_success(
                "gmail",
                sync_cursor=result.provider_history_cursor,
            )
            error_code = None
        else:
            self._dependencies.store.record_sync_failure(
                "gmail",
                error_code="degraded",
            )
            error_code = "degraded"
        return _SourceReadOutcome(
            count=len(result.threads),
            ids=tuple(thread.thread_id for thread in result.threads),
            error_code=error_code,
        )

    def _sync_calendar(self) -> _SourceReadOutcome:
        try:
            result = self._dependencies.calendar.list_events()
        except (CalendarError, GoogleTransportError) as error:
            return self._record_failure("calendar", error.error_code)
        if result.skipped_count == 0:
            self._dependencies.store.record_sync_success("calendar")
            error_code = None
        else:
            self._dependencies.store.record_sync_failure(
                "calendar",
                error_code="degraded",
            )
            error_code = "degraded"
        return _SourceReadOutcome(
            count=len(result.events),
            ids=tuple(event.id for event in result.events),
            error_code=error_code,
        )

    def _record_failure(
        self,
        source: Literal["gmail", "calendar"],
        error_code: SourceSyncFailureCode,
    ) -> _SourceReadOutcome:
        self._dependencies.store.record_sync_failure(source, error_code=error_code)
        return _SourceReadOutcome(count=0, ids=(), error_code=error_code)

    def _reauthentication_summary(self) -> GoogleReadSummary:
        self._dependencies.store.record_google_invalid_grant()
        cleanup_failed = False
        try:
            self._dependencies.credentials.delete()
        except CredentialStorageError:
            cleanup_failed = True
        return GoogleReadSummary(
            gmail_count=0,
            gmail_ids=(),
            gmail_error_code="invalid_grant",
            calendar_count=0,
            calendar_ids=(),
            calendar_error_code="invalid_grant",
            credential_cleanup_failed=cleanup_failed,
        )


def _summary(
    gmail: _SourceReadOutcome,
    calendar: _SourceReadOutcome,
) -> GoogleReadSummary:
    """Translate internal source outcomes to a redacted, public result."""
    return GoogleReadSummary(
        gmail_count=gmail.count,
        gmail_ids=gmail.ids,
        gmail_error_code=gmail.error_code,
        calendar_count=calendar.count,
        calendar_ids=calendar.ids,
        calendar_error_code=calendar.error_code,
    )


__all__ = [
    "CalendarEventsReader",
    "GmailProfileReader",
    "GoogleCredentialStore",
    "GoogleReadDependencies",
    "GoogleReadSmokeDisabledError",
    "GoogleReadSummary",
    "GoogleSyncService",
    "GoogleTransportError",
    "InvalidGrantError",
]
