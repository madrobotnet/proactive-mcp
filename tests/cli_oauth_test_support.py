from dataclasses import dataclass, field
from pathlib import Path

import pytest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import WSGITimeoutError
from typing_extensions import override

from proactive_mcp.sources import GoogleOAuthAuthorizer
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStore,
    GoogleCredential,
)
from proactive_mcp.sources.oauth import (
    HEADLESS_AUTHORIZATION_URL_EVENT,
    HEADLESS_SETUP_SUCCESS_EVENT,
    GoogleClientConfig,
    write_headless_authorization_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "auth"
_TEST_ACCESS = "access" + "-token"
_TEST_REFRESH = "refresh" + "-token"
_TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
_TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client" + "-secret"
_FAKE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/auth"
    "?client_id=test-client.apps.googleusercontent.com"
    "&redirect_uri=http%3A%2F%2F127.0.0.1"
)
_AUTHORIZATION_URL_NEEDLE = "https://accounts.google.com/o/oauth2/auth"


@dataclass(frozen=True, slots=True)
class FakeKeyring:
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
        write_headless_authorization_url(_FAKE_AUTHORIZATION_URL)
        return self.credentials


class FakeFlowFactory:
    flow: FakeInstalledAppFlow
    client_config: GoogleClientConfig | None
    scopes: tuple[str, str] | None

    def __init__(self, flow: FakeInstalledAppFlow) -> None:
        self.flow = flow
        self.client_config = None
        self.scopes = None

    def from_client_config(
        self,
        client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> FakeInstalledAppFlow:
        self.client_config = client_config
        self.scopes = scopes
        return self.flow


class ErrorInstalledAppFlow(FakeInstalledAppFlow):
    error: BaseException

    def __init__(self, error: BaseException) -> None:
        super().__init__(google_credential())
        self.error = error

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
        self.calls.append(FlowCall(host, port, open_browser, timeout_seconds, prompt))
        raise self.error


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
        self.calls.append(FlowCall(host, port, open_browser, timeout_seconds, prompt))
        write_headless_authorization_url(_FAKE_AUTHORIZATION_URL)
        raise WSGITimeoutError


def google_credential(*, refresh_token: str | None = _TEST_REFRESH) -> GoogleCredential:
    return Credentials(
        token=_TEST_ACCESS,
        refresh_token=refresh_token,
        token_uri=_TEST_TOKEN_URI,
        client_id=_TEST_CLIENT_ID,
        client_secret=_TEST_CLIENT_SECRET,
        scopes=list(GOOGLE_READONLY_SCOPES),
    )


def count_authorization_url_events(*parts: str) -> int:
    return sum(
        line.startswith(HEADLESS_AUTHORIZATION_URL_EVENT)
        or _AUTHORIZATION_URL_NEEDLE in line
        for text in parts
        for line in text.splitlines()
    )


def count_setup_success_events(*parts: str) -> int:
    return sum(
        line == HEADLESS_SETUP_SUCCESS_EVENT
        for text in parts
        for line in text.splitlines()
    )


def install_fake_authorizer(
    monkeypatch: pytest.MonkeyPatch, flow: FakeInstalledAppFlow
) -> None:
    factory = FakeFlowFactory(flow)

    def build(store: CredentialStore) -> GoogleOAuthAuthorizer:
        return GoogleOAuthAuthorizer(store, flow_factory=factory)

    def credential_store(path: Path) -> CredentialStore:
        return CredentialStore(path, keyring=FakeKeyring())

    monkeypatch.setattr("proactive_mcp.sources.GoogleOAuthAuthorizer", build)
    monkeypatch.setattr("proactive_mcp.sources.CredentialStore", credential_store)
