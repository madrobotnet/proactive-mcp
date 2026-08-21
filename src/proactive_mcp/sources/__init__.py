"""External source adapters and read-only synchronization orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from typing_extensions import override

from proactive_mcp.clock import UtcClock
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
    from pathlib import Path


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

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> GmailHttpResponse:
        """Perform one Gmail GET and return its adapter-specific response type."""
        del method
        response = self.transport.get(url, query)
        return GmailHttpResponse(status_code=response.status_code, body=response.body)


@dataclass(frozen=True, slots=True)
class _CalendarReadTransport:
    """Adapt the shared transport to Calendar's nominal response contract."""

    transport: GoogleAuthenticatedGetTransport

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> CalendarHttpResponse:
        """Perform one Calendar GET and return its adapter-specific response type."""
        del method
        response = self.transport.get(url, query)
        return CalendarHttpResponse(
            status_code=response.status_code,
            body=response.body,
        )


def run_google_read_smoke(
    database_path: Path,
    *,
    enabled: bool,
) -> GoogleReadSummary:
    """Read Gmail and Calendar only after explicit real-account confirmation."""
    if not enabled:
        raise GoogleReadSmokeDisabledError
    credential_store = CredentialStore(database_path.parent)
    credentials = credential_store.load()
    if credentials is None:
        raise MissingGoogleCredentialsError
    transport = GoogleAuthenticatedGetTransport(credentials)
    clock = UtcClock()
    with Store(database_path) as store:
        return GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=GmailAdapter(_GmailReadTransport(transport), clock),
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
