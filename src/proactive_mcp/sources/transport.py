"""Concrete authenticated GET transport for Google read adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, cast

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
    from collections.abc import Iterator, Mapping
    from typing import Literal

    from proactive_mcp.sources.credentials import GoogleCredential

_REQUEST_TIMEOUT_SECONDS: Final[int] = 30
_NETWORK_ERROR: Final[GoogleTransportErrorCode] = "network"
_TIMEOUT_ERROR: Final[GoogleTransportErrorCode] = "timeout"
_UNKNOWN_ERROR: Final[GoogleTransportErrorCode] = "unknown"
DEFAULT_MAX_RESPONSE_BYTES: Final[int] = 1_000_000
_STREAM_CHUNK_BYTES: Final[int] = 64 * 1024


@dataclass(frozen=True, slots=True)
class GoogleHttpResponse:
    """The status and bytes returned by a read-only authenticated request."""

    status_code: int
    body: bytes
    too_large: bool = False


class AuthorizedHttpSession(Protocol):
    """Perform one authenticated GET request through google-auth."""

    def get(
        self,
        url: str,
        query: Mapping[str, str],
        *,
        max_bytes: int,
    ) -> GoogleHttpResponse:
        """Request a read-only Google endpoint."""
        ...


class _StreamingHttpResponse(Protocol):
    """The small requests response surface used by bounded streaming."""

    @property
    def status_code(self) -> int:
        """Return the HTTP status code."""
        ...

    @property
    def headers(self) -> Mapping[str, str]:
        """Return response headers."""
        ...

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:
        """Yield decoded response chunks."""
        ...

    def close(self) -> None:
        """Release the underlying HTTP connection."""
        ...


class _RawAuthorizedSession(Protocol):
    """Refresh-aware request call before adaptation to the public contract."""

    def request(
        self,
        method: Literal["GET"],
        url: str,
        *,
        params: Mapping[str, str],
        timeout: int,
        stream: bool,
    ) -> _StreamingHttpResponse:
        """Open one authenticated streaming response."""
        ...


@dataclass(frozen=True, slots=True)
class _GoogleAuthorizedSession:
    """Adapt google-auth's refresh-aware session to the narrow GET contract."""

    session: _RawAuthorizedSession

    def get(
        self,
        url: str,
        query: Mapping[str, str],
        *,
        max_bytes: int,
    ) -> GoogleHttpResponse:
        """Stream at most ``max_bytes`` decoded response bytes."""
        response = self.session.request(
            "GET",
            url,
            params=query,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            stream=True,
        )
        try:
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        return GoogleHttpResponse(
                            response.status_code,
                            b"",
                            too_large=True,
                        )
                except ValueError:
                    pass
            body = bytearray()
            for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
                if not chunk:
                    continue
                remaining = max_bytes + 1 - len(body)
                body.extend(chunk[:remaining])
                if len(body) > max_bytes:
                    return GoogleHttpResponse(
                        response.status_code,
                        b"",
                        too_large=True,
                    )
            return GoogleHttpResponse(response.status_code, bytes(body))
        finally:
            response.close()


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
            _GoogleAuthorizedSession(
                cast(
                    "_RawAuthorizedSession",
                    cast("object", AuthorizedSession(credentials)),
                )
            )
            if session is None
            else session,
        )

    def get(
        self,
        url: str,
        query: Mapping[str, str],
        *,
        max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> GoogleHttpResponse:
        """Perform a single authenticated GET and safely map revoked grants."""
        if max_bytes < 1:
            raise GoogleTransportError(_UNKNOWN_ERROR)
        try:
            return self.session.get(url, query, max_bytes=max_bytes)
        except RefreshError as error:
            if "invalid_grant" in str(error):
                raise InvalidGrantError from None
            raise GoogleTransportError(_UNKNOWN_ERROR) from None
        except RequestsTimeout:
            raise GoogleTransportError(_TIMEOUT_ERROR) from None
        except RequestsConnectionError:
            raise GoogleTransportError(_NETWORK_ERROR) from None


__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "GoogleAuthenticatedGetTransport",
    "GoogleHttpResponse",
]
