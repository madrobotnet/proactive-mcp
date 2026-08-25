"""External source adapters and read-only synchronization orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias, final

from typing_extensions import override

from proactive_mcp.clock import UtcClock
from proactive_mcp.config import load_config
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.store import Store

from .calendar import CalendarAdapter, CalendarHttpResponse
from .credentials import (
    CredentialScopeError,
    CredentialStorageError,
    CredentialStore,
    MissingRefreshTokenError,
)
from .gmail import GmailAdapter, GmailHttpResponse
from .google_sync import (
    CalendarEventsReader,
    GmailProfileReader,
    GoogleCredentialStore,
    GoogleReadDependencies,
    GoogleReadSmokeDisabledError,
    GoogleReadSummary,
    GoogleSyncService,
    InvalidGrantError,
)
from .oauth import (
    GoogleOAuthAuthorizationTimeoutError,
    GoogleOAuthAuthorizer,
    OAuthClientConfigError,
)
from .transport import GoogleAuthenticatedGetTransport

if TYPE_CHECKING:
    from datetime import timedelta
    from pathlib import Path

    from proactive_mcp.clock import Clock
    from proactive_mcp.sources.credentials import GoogleCredential


@dataclass(frozen=True, slots=True)
class GoogleSetupOptions:
    """The parsed OAuth controls accepted by the setup command."""

    client_secrets_path: Path
    reauth: bool
    headless: bool


@dataclass(frozen=True, slots=True)
class MissingGoogleCredentialsError(Exception):
    """Signal that a read cannot begin without a stored OAuth credential."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe operator message."""
        return "Google credentials are missing; run proactive-mcp setup"


_LimitReason: TypeAlias = Literal["response", "sync"]
_MAX_GMAIL_PROFILE_BYTES = 64 * 1024
_MAX_GMAIL_PAGE_BYTES = 1_000_000
_MAX_GMAIL_THREAD_BYTES = 1_000_000
_MAX_CALENDAR_PAGE_BYTES = 2_000_000
_MAX_SOURCE_SYNC_BYTES = 8_000_000
_MAX_SOURCE_SYNC_REQUESTS = 64


@final
class _SourceReadBudget:
    """Bound cumulative allocation and request work for one source pass."""

    __slots__ = ("bytes_remaining", "requests_remaining")

    bytes_remaining: int
    requests_remaining: int

    def __init__(self) -> None:
        self.bytes_remaining = _MAX_SOURCE_SYNC_BYTES
        self.requests_remaining = _MAX_SOURCE_SYNC_REQUESTS

    def request_limit(self, endpoint_limit: int) -> int:
        if self.requests_remaining <= 0 or self.bytes_remaining <= 0:
            return 0
        self.requests_remaining -= 1
        return min(endpoint_limit, self.bytes_remaining)

    def consume(self, size: int) -> None:
        self.bytes_remaining = max(0, self.bytes_remaining - size)


def configure_google_sources(
    database_path: Path,
    options: GoogleSetupOptions,
) -> None:
    """Authorize Google read-only access and configure both shared sources."""
    credential_store = CredentialStore(database_path.parent)
    _ = GoogleOAuthAuthorizer(credential_store).authorize(
        options.client_secrets_path,
        reauth=options.reauth,
        headless=options.headless,
    )
    with Store(database_path) as store:
        store.set_google_auth_state("configured")


@dataclass(frozen=True, slots=True)
class _GmailReadTransport:
    """Adapt the shared transport to Gmail's nominal response contract."""

    transport: GoogleAuthenticatedGetTransport
    budget: _SourceReadBudget = field(default_factory=_SourceReadBudget)

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> GmailHttpResponse:
        """Perform one Gmail GET and return its adapter-specific response type."""
        del method
        endpoint_limit = (
            _MAX_GMAIL_PROFILE_BYTES
            if url.endswith("/profile")
            else _MAX_GMAIL_THREAD_BYTES
            if "/threads/" in url
            else _MAX_GMAIL_PAGE_BYTES
        )
        allowed = self.budget.request_limit(endpoint_limit)
        if allowed == 0:
            return GmailHttpResponse(200, b"", limit_reason="sync")
        response = self.transport.get(url, query, max_bytes=allowed)
        limit_reason: _LimitReason | None = None
        if response.too_large:
            self.budget.consume(allowed)
            limit_reason = "sync" if allowed < endpoint_limit else "response"
        else:
            self.budget.consume(len(response.body))
        return GmailHttpResponse(
            status_code=response.status_code,
            body=response.body,
            limit_reason=limit_reason,
        )


@dataclass(frozen=True, slots=True)
class _CalendarReadTransport:
    """Adapt the shared transport to Calendar's nominal response contract."""

    transport: GoogleAuthenticatedGetTransport
    budget: _SourceReadBudget = field(default_factory=_SourceReadBudget)

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> CalendarHttpResponse:
        """Perform one Calendar GET and return its adapter-specific response type."""
        del method
        allowed = self.budget.request_limit(_MAX_CALENDAR_PAGE_BYTES)
        if allowed == 0:
            return CalendarHttpResponse(200, b"", limit_reason="sync")
        response = self.transport.get(url, query, max_bytes=allowed)
        limit_reason: _LimitReason | None = None
        if response.too_large:
            self.budget.consume(allowed)
            limit_reason = "sync" if allowed < _MAX_CALENDAR_PAGE_BYTES else "response"
        else:
            self.budget.consume(len(response.body))
        return CalendarHttpResponse(
            status_code=response.status_code,
            body=response.body,
            limit_reason=limit_reason,
        )


@dataclass(frozen=True, slots=True)
class GoogleReadServiceFactory:
    """Build one authenticated read service per loaded credential."""

    store: Store
    clock: Clock
    credentials: CredentialStore
    gmail_lookback: timedelta

    def open(self, credential: GoogleCredential) -> GoogleSyncService:
        """Bind the read adapters of both sources to one credential."""
        transport = GoogleAuthenticatedGetTransport(credential)
        return GoogleSyncService(
            GoogleReadDependencies(
                store=self.store,
                gmail=GmailAdapter(
                    _GmailReadTransport(transport),
                    self.clock,
                    lookback=self.gmail_lookback,
                ),
                calendar=CalendarAdapter(_CalendarReadTransport(transport), self.clock),
                credentials=self.credentials,
            )
        )


def run_google_read_smoke(
    database_path: Path,
    *,
    enabled: bool,
) -> GoogleReadSummary:
    """Read Gmail and Calendar only after explicit real-account confirmation."""
    if not enabled:
        raise GoogleReadSmokeDisabledError
    paths = ProactivePaths.for_database(database_path)
    config = load_config(paths.config)
    credential_store = CredentialStore(paths.state_directory)
    credentials = credential_store.load()
    if credentials is None:
        raise MissingGoogleCredentialsError
    transport = GoogleAuthenticatedGetTransport(credentials)
    clock = UtcClock()
    with Store(paths.database) as store:
        return GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=GmailAdapter(
                    _GmailReadTransport(transport),
                    clock,
                    lookback=config.sources.gmail_lookback,
                ),
                calendar=CalendarAdapter(_CalendarReadTransport(transport), clock),
                credentials=credential_store,
            )
        ).read_smoke(enabled=True)


__all__ = [
    "CalendarEventsReader",
    "CredentialScopeError",
    "CredentialStorageError",
    "GmailProfileReader",
    "GoogleCredentialStore",
    "GoogleOAuthAuthorizationTimeoutError",
    "GoogleOAuthAuthorizer",
    "GoogleReadDependencies",
    "GoogleReadServiceFactory",
    "GoogleReadSmokeDisabledError",
    "GoogleReadSummary",
    "GoogleSetupOptions",
    "GoogleSyncService",
    "InvalidGrantError",
    "MissingGoogleCredentialsError",
    "MissingRefreshTokenError",
    "OAuthClientConfigError",
    "configure_google_sources",
    "run_google_read_smoke",
]
