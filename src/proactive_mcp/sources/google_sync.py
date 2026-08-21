"""Read-only Gmail and Calendar synchronization orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from typing_extensions import override

from proactive_mcp.sources.calendar import CalendarError
from proactive_mcp.sources.credentials import CredentialStorageError
from proactive_mcp.sources.gmail import GmailError

if TYPE_CHECKING:
    from proactive_mcp.sources.calendar import CalendarReadResult
    from proactive_mcp.sources.gmail import GmailProfile
    from proactive_mcp.store import SourceErrorCode, SourceSyncFailureCode, Store


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
    """Read the authenticated Gmail profile without exposing it downstream."""

    def read_profile(self) -> GmailProfile:
        """Return the authenticated Gmail profile."""
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

    def _sync_gmail(self) -> _SourceReadOutcome:
        try:
            profile = self._dependencies.gmail.read_profile()
        except (GmailError, GoogleTransportError) as error:
            return self._record_failure("gmail", error.error_code)
        self._dependencies.store.record_sync_success("gmail")
        return _SourceReadOutcome(count=profile.threads_total, ids=(), error_code=None)

    def _sync_calendar(self) -> _SourceReadOutcome:
        try:
            result = self._dependencies.calendar.list_events()
        except (CalendarError, GoogleTransportError) as error:
            return self._record_failure("calendar", error.error_code)
        self._dependencies.store.record_sync_success("calendar")
        return _SourceReadOutcome(
            count=len(result.events),
            ids=tuple(event.id for event in result.events),
            error_code=None,
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
