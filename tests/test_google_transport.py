from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

import proactive_mcp.sources.transport as transport_module
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    GoogleCredential,
)
from proactive_mcp.sources.google_sync import (
    GoogleTransportError,
    GoogleTransportErrorCode,
    InvalidGrantError,
)
from proactive_mcp.sources.transport import (
    GoogleAuthenticatedGetTransport,
    GoogleHttpResponse,
)

_TEST_ACCESS = "access" + "-token"
_EAGER_CONTENT_ERROR = "streaming transport accessed eager response.content"
_TEST_REFRESH = "refresh" + "-token"
_TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
_TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client" + "-secret"

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import Literal


class FakeAuthorizedSession:
    calls: list[tuple[str, str, dict[str, str], float]]
    error: RefreshError | RequestsConnectionError | RequestsTimeout | None

    def __init__(
        self,
        *,
        error: RefreshError | RequestsConnectionError | RequestsTimeout | None = None,
    ) -> None:
        self.calls = []
        self.error = error

    def get(
        self,
        url: str,
        query: Mapping[str, str],
        *,
        max_bytes: int,
    ) -> GoogleHttpResponse:
        if self.error is not None:
            raise self.error
        assert max_bytes == 1_000_000
        self.calls.append(("GET", url, dict(query), 30.0))
        return GoogleHttpResponse(status_code=200, body=b'{"ok":true}')


class FakeStreamingResponse:
    status_code: int
    headers: dict[str, str]
    chunks: tuple[bytes, ...]
    yielded: int
    closed: bool

    def __init__(
        self,
        chunks: tuple[bytes, ...],
        *,
        content_length: str | None = None,
    ) -> None:
        self.status_code = 200
        self.headers = (
            {} if content_length is None else {"Content-Length": content_length}
        )
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    @property
    def content(self) -> bytes:
        raise AssertionError(_EAGER_CONTENT_ERROR)

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:
        assert chunk_size == 64 * 1024
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeRawAuthorizedSession:
    response: FakeStreamingResponse

    def __init__(self, response: FakeStreamingResponse) -> None:
        self.response = response

    def request(
        self,
        method: Literal["GET"],
        url: str,
        *,
        params: Mapping[str, str],
        timeout: int,
        stream: bool,
    ) -> FakeStreamingResponse:
        assert method == "GET"
        assert url == "https://example.test/read"
        assert params == {}
        assert timeout == 30
        assert stream is True
        return self.response


def _credentials() -> GoogleCredential:
    return Credentials(
        token=_TEST_ACCESS,
        refresh_token=_TEST_REFRESH,
        token_uri=_TEST_TOKEN_URI,
        client_id=_TEST_CLIENT_ID,
        client_secret=_TEST_CLIENT_SECRET,
        scopes=list(GOOGLE_READONLY_SCOPES),
    )


def test_authenticated_transport_exposes_only_get() -> None:
    session = FakeAuthorizedSession()
    transport = GoogleAuthenticatedGetTransport(_credentials(), session=session)

    response = transport.get("https://example.test/read", {"fields": "id"})

    assert response.status_code == 200
    assert response.body == b'{"ok":true}'
    assert session.calls == [
        ("GET", "https://example.test/read", {"fields": "id"}, 30.0)
    ]
    assert not hasattr(transport, "request")


def test_production_session_stops_at_limit_plus_one_without_eager_body() -> None:
    response = FakeStreamingResponse((b"abc", b"def", b"unread"))
    raw = FakeRawAuthorizedSession(response)
    session = transport_module._GoogleAuthorizedSession(raw)  # pyright: ignore[reportPrivateUsage]

    result = session.get("https://example.test/read", {}, max_bytes=5)

    assert result.too_large is True
    assert result.body == b""
    assert response.yielded == 2
    assert response.closed is True


def test_declared_oversize_is_rejected_before_body_consumption() -> None:
    response = FakeStreamingResponse((b"never-read",), content_length="6")
    raw = FakeRawAuthorizedSession(response)
    session = transport_module._GoogleAuthorizedSession(raw)  # pyright: ignore[reportPrivateUsage]

    result = session.get("https://example.test/read", {}, max_bytes=5)

    assert result.too_large is True
    assert response.yielded == 0
    assert response.closed is True


def test_authenticated_transport_maps_invalid_grant() -> None:
    transport = GoogleAuthenticatedGetTransport(
        _credentials(),
        session=FakeAuthorizedSession(error=RefreshError("invalid_grant")),
    )

    with pytest.raises(InvalidGrantError) as error:
        _ = transport.get("https://example.test/read", {})

    assert isinstance(error.value, InvalidGrantError)


@pytest.mark.parametrize(
    ("transport_error", "expected_code"),
    [
        (RequestsTimeout(), "timeout"),
        (RequestsConnectionError(), "network"),
        (RefreshError("refresh_failed"), "unknown"),
    ],
)
def test_authenticated_transport_normalizes_safe_read_failures(
    transport_error: RefreshError | RequestsConnectionError | RequestsTimeout,
    expected_code: GoogleTransportErrorCode,
) -> None:
    transport = GoogleAuthenticatedGetTransport(
        _credentials(),
        session=FakeAuthorizedSession(error=transport_error),
    )

    with pytest.raises(GoogleTransportError) as error:
        _ = transport.get("https://example.test/read", {})

    assert error.value.error_code == expected_code
