"""Read-only Gmail and Calendar synchronization orchestration."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias

from typing_extensions import override

from proactive_mcp.sources._google_evaluation import prepare_evaluation
from proactive_mcp.sources.calendar import CalendarError
from proactive_mcp.sources.credentials import CredentialStorageError
from proactive_mcp.sources.gmail import GmailError
from proactive_mcp.store.sync import (
    GMAIL_READ_BYTE_BUDGET,
    SourceReadDiagnostics,
    SourceReadReason,
    SourceReadReasonCount,
    source_failure_diagnostics,
)

if TYPE_CHECKING:
    from proactive_mcp.situations.inputs import EngineInputs
    from proactive_mcp.sources.calendar import CalendarReadResult
    from proactive_mcp.sources.gmail import GmailInboxReadResult
    from proactive_mcp.store import (
        SourceErrorCode,
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


_LEGACY_GMAIL_DIAGNOSTICS: Final = SourceReadDiagnostics(
    outcome="stale",
    request_count=0,
    page_count=0,
    projected_count=0,
    excluded_count=0,
    byte_budget=GMAIL_READ_BYTE_BUDGET,
)


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
    gmail_diagnostics: SourceReadDiagnostics = _LEGACY_GMAIL_DIAGNOSTICS


@dataclass(frozen=True, slots=True)
class _SourceReadOutcome:
    """Internal redacted outcome for one source read."""

    count: int
    ids: tuple[str, ...]
    error_code: SourceSyncFailureCode | None


@dataclass(frozen=True, slots=True)
class _GmailReadOutcome(_SourceReadOutcome):
    """Internal Gmail outcome with its bounded read diagnostics."""

    diagnostics: SourceReadDiagnostics


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
        return prepare_evaluation(
            self._dependencies,
            InvalidGrantError,
            GoogleTransportError,
            _gmail_read_diagnostics,
            _transport_error_code,
        )

    def _sync_gmail(self) -> _GmailReadOutcome:
        try:
            result = self._dependencies.gmail.read_inbox_threads()
        except (GmailError, GoogleTransportError) as error:
            diagnostics = source_failure_diagnostics(error.error_code)
            self._dependencies.store.record_gmail_sync(
                diagnostics,
                error_code=error.error_code,
            )
            return _GmailReadOutcome(
                count=0,
                ids=(),
                error_code=error.error_code,
                diagnostics=diagnostics,
            )
        diagnostics = _gmail_read_diagnostics(result)
        error_code: SourceSyncFailureCode | None = (
            None if result.coverage_complete else "degraded"
        )
        self._dependencies.store.record_gmail_sync(
            diagnostics,
            sync_cursor=result.provider_history_cursor,
            error_code=error_code,
        )
        return _GmailReadOutcome(
            count=len(result.threads),
            ids=tuple(thread.thread_id for thread in result.threads),
            error_code=error_code,
            diagnostics=diagnostics,
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
        diagnostics = source_failure_diagnostics("invalid_grant")
        self._dependencies.store.record_google_invalid_grant(diagnostics)
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
            gmail_diagnostics=diagnostics,
        )


def _transport_error_code(error: Exception) -> GoogleTransportErrorCode:
    if not isinstance(error, GoogleTransportError):
        raise TypeError
    return error.error_code


def _summary(
    gmail: _GmailReadOutcome,
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
        gmail_diagnostics=gmail.diagnostics,
    )


def _gmail_read_diagnostics(result: GmailInboxReadResult) -> SourceReadDiagnostics:
    """Summarize bounded Gmail work without retaining source values."""
    counts: Counter[SourceReadReason] = Counter(result.degradation_reasons)
    reason_counts = (
        tuple(
            SourceReadReasonCount(reason, count)
            for reason, count in result.degradation_reason_counts
        )
        if result.degradation_reason_counts
        else tuple(
            SourceReadReasonCount(reason, count) for reason, count in counts.items()
        )
    )
    return SourceReadDiagnostics(
        outcome="healthy" if result.coverage_complete else "partial",
        request_count=result.request_count,
        page_count=result.page_count,
        projected_count=result.projected_thread_count,
        excluded_count=result.excluded_thread_count,
        byte_budget=GMAIL_READ_BYTE_BUDGET,
        reason_counts=reason_counts,
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
