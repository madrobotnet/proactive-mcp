"""Concrete authenticated GET transport for Google read adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from proactive_mcp.sources.google_sync import (
    GoogleTransportError,
    GoogleTransportErrorCode,
    InvalidGrantError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from proactive_mcp.sources.credentials import GoogleCredential

_REQUEST_TIMEOUT_SECONDS: Final[int] = 30
_NETWORK_ERROR: Final[GoogleTransportErrorCode] = "network"
_TIMEOUT_ERROR: Final[GoogleTransportErrorCode] = "timeout"
_UNKNOWN_ERROR: Final[GoogleTransportErrorCode] = "unknown"


@dataclass(frozen=True, slots=True)
class GoogleHttpResponse:
    """The status and bytes returned by a read-only authenticated request."""

    status_code: int
    body: bytes


class AuthorizedHttpSession(Protocol):
    """Perform one authenticated GET request through google-auth."""

    def get(
        self,
        url: str,
        query: Mapping[str, str],
    ) -> GoogleHttpResponse:
        """Request a read-only Google endpoint."""
        ...


@dataclass(frozen=True, slots=True)
class _GoogleAuthorizedSession:
    """Adapt google-auth's refresh-aware session to the narrow GET contract."""

    session: AuthorizedSession

    def get(
        self,
        url: str,
        query: Mapping[str, str],
    ) -> GoogleHttpResponse:
        """Perform one bounded authenticated request with query parameters."""
        response = self.session.request(
            "GET",
            url,
            params=query,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        return GoogleHttpResponse(
            status_code=response.status_code,
            body=response.content,
        )


@dataclass(frozen=True, slots=True)
class GoogleAuthenticatedGetTransport:
    """Use google-auth's refresh-aware session for GET-only source requests."""

    session: AuthorizedHttpSession

    def __init__(
        self,
        credentials: GoogleCredential,
        *,
        session: AuthorizedHttpSession | None = None,
    ) -> None:
        """Bind one authenticated session, constructing it for production by default."""
        object.__setattr__(
            self,
            "session",
            _GoogleAuthorizedSession(AuthorizedSession(credentials))
            if session is None
            else session,
        )

    def get(
        self,
        url: str,
        query: Mapping[str, str],
    ) -> GoogleHttpResponse:
        """Perform a single authenticated GET and safely map revoked grants."""
        try:
            return self.session.get(url, query)
        except RefreshError as error:
            if "invalid_grant" in str(error):
                raise InvalidGrantError from None
            raise GoogleTransportError(_UNKNOWN_ERROR) from None
        except RequestsTimeout:
            raise GoogleTransportError(_TIMEOUT_ERROR) from None
        except RequestsConnectionError:
            raise GoogleTransportError(_NETWORK_ERROR) from None


__all__ = ["GoogleAuthenticatedGetTransport", "GoogleHttpResponse"]
