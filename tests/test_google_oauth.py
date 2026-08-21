from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import WSGITimeoutError
from typing_extensions import override

from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStore,
    GoogleCredential,
    MissingRefreshTokenError,
)
from proactive_mcp.sources.oauth import (
    GoogleClientConfig,
    GoogleOAuthAuthorizationTimeoutError,
    GoogleOAuthAuthorizer,
)

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "auth"
_TEST_ACCESS = "access" + "-token"
_TEST_REFRESH = "refresh" + "-token"
_TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
_TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client" + "-secret"


@dataclass(frozen=True, slots=True)
class FakeKeyring:
    """Record credential replacement order for OAuth tests."""

    passwords: dict[tuple[str, str], str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def get_password(self, service_name: str, username: str) -> str | None:
        self.calls.append("get")
        return self.passwords.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.calls.append("set")
        self.passwords[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.calls.append("delete")
        _ = self.passwords.pop((service_name, username), None)


@dataclass(frozen=True, slots=True)
class FlowCall:
    host: str
    port: int
    open_browser: bool
    timeout_seconds: int
    prompt: str | None


class FakeInstalledAppFlow:
    credentials: GoogleCredential
    calls: list[FlowCall]

    def __init__(self, credentials: GoogleCredential) -> None:
        self.credentials = credentials
        self.calls = []

    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        prompt: str | None = None,
    ) -> GoogleCredential:
        self.calls.append(FlowCall(host, port, open_browser, timeout_seconds, prompt))
        return self.credentials


class FakeFlowFactory:
    flow: FakeInstalledAppFlow
    scopes: tuple[str, str] | None

    def __init__(self, flow: FakeInstalledAppFlow) -> None:
        self.flow = flow
        self.scopes = None

    def from_client_config(
        self,
        client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> FakeInstalledAppFlow:
        del client_config
        self.scopes = scopes
        return self.flow


class TimeoutInstalledAppFlow(FakeInstalledAppFlow):
    @override
    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        prompt: str | None = None,
    ) -> GoogleCredential:
        del host, port, open_browser, timeout_seconds, prompt
        raise WSGITimeoutError


def _credentials(*, refresh_token: str | None = _TEST_REFRESH) -> GoogleCredential:
    return Credentials(
        token=_TEST_ACCESS,
        refresh_token=refresh_token,
        token_uri=_TEST_TOKEN_URI,
        client_id=_TEST_CLIENT_ID,
        client_secret=_TEST_CLIENT_SECRET,
        scopes=list(GOOGLE_READONLY_SCOPES),
    )


def test_reauth_preserves_stale_credentials_until_consent_succeeds(
    tmp_path: Path,
) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path / "state", keyring=keyring)
    store.save(_credentials())
    flow = FakeInstalledAppFlow(_credentials())
    factory = FakeFlowFactory(flow)
    authorizer = GoogleOAuthAuthorizer(store, flow_factory=factory)

    authorized = authorizer.authorize(
        FIXTURES / "installed-client.json", reauth=True, headless=True
    )

    assert authorized.refresh_token == _TEST_REFRESH
    assert keyring.calls == ["set", "set"]
    assert factory.scopes == GOOGLE_READONLY_SCOPES
    assert flow.calls == [
        FlowCall(
            host="127.0.0.1",
            port=0,
            open_browser=False,
            timeout_seconds=300,
            prompt="consent",
        )
    ]


def test_authorization_rejects_a_flow_without_a_refresh_token(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(
            FakeInstalledAppFlow(_credentials(refresh_token=None))
        ),
    )

    with pytest.raises(MissingRefreshTokenError):
        _ = authorizer.authorize(FIXTURES / "installed-client.json")

    assert store.load() is None


def test_authorization_timeout_returns_a_typed_error(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(TimeoutInstalledAppFlow(_credentials())),
    )

    with pytest.raises(GoogleOAuthAuthorizationTimeoutError) as error:
        _ = authorizer.authorize(
            FIXTURES / "installed-client.json",
            headless=True,
        )

    assert isinstance(error.value, GoogleOAuthAuthorizationTimeoutError)
