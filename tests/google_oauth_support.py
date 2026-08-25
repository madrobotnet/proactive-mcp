from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import WSGITimeoutError
from typing_extensions import override

from proactive_mcp.sources.credentials import GOOGLE_READONLY_SCOPES, GoogleCredential
from proactive_mcp.sources.oauth import (
    HEADLESS_AUTHORIZATION_URL_EVENT,
    HEADLESS_SETUP_SUCCESS_EVENT,
    GoogleClientConfig,
    write_headless_authorization_url,
)

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse, WSGIEnvironment

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "auth"
TEST_ACCESS = "access" + "-token"
TEST_REFRESH = "refresh" + "-token"
TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
TEST_CLIENT_SECRET = "test-client" + "-secret"
TEST_BOOTSTRAP_CLIENT_SECRET = "sanitized-test-client" + "-secret"
FAKE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/auth"
    "?client_id=test-client.apps.googleusercontent.com"
    "&redirect_uri=http%3A%2F%2F127.0.0.1"
)
AUTHORIZATION_URL_NEEDLE = "https://accounts.google.com/o/oauth2/auth"


def count_authorization_url_events(*parts: str) -> int:
    count = 0
    for text in parts:
        for line in text.splitlines():
            if line.startswith(HEADLESS_AUTHORIZATION_URL_EVENT) or (
                AUTHORIZATION_URL_NEEDLE in line
            ):
                count += 1
    return count


def count_setup_success_events(*parts: str) -> int:
    count = 0
    for text in parts:
        for line in text.splitlines():
            if line == HEADLESS_SETUP_SUCCESS_EVENT:
                count += 1
    return count


class CallbackCanaryApplication:
    """Mutable WSGI callback recorder synchronized with the handler thread."""

    callback_received: threading.Event
    request_target: str | None

    def __init__(self) -> None:
        self.callback_received = threading.Event()
        self.request_target = None

    def __call__(
        self,
        environ: WSGIEnvironment,
        start_response: StartResponse,
    ) -> list[bytes]:
        self.request_target = f"{environ['PATH_INFO']}?{environ['QUERY_STRING']}"
        self.callback_received.set()
        _ = start_response(
            "200 OK",
            [("Content-Type", "text/plain; charset=utf-8")],
        )
        return [b"callback complete"]


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
        write_headless_authorization_url(FAKE_AUTHORIZATION_URL)
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


class AuthorizationPrompt(Protocol):
    def format(self, **kwargs: str) -> str: ...


class RecordingOauthlibFlow:
    """Spy for the real adapter's oauthlib run_local_server call."""

    credentials: GoogleCredential
    authorization_prompts: list[AuthorizationPrompt | None]

    def __init__(self, credentials: GoogleCredential) -> None:
        self.credentials = credentials
        self.authorization_prompts = []

    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        prompt: str | None = None,
        **extra: AuthorizationPrompt,
    ) -> GoogleCredential:
        del host, port, open_browser, timeout_seconds, prompt
        self.authorization_prompts.append(extra.get("authorization_prompt_message"))
        return self.credentials


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
        write_headless_authorization_url(FAKE_AUTHORIZATION_URL)
        raise WSGITimeoutError


def google_credential(*, refresh_token: str | None = TEST_REFRESH) -> GoogleCredential:
    return Credentials(
        token=TEST_ACCESS,
        refresh_token=refresh_token,
        token_uri=TEST_TOKEN_URI,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        scopes=list(GOOGLE_READONLY_SCOPES),
    )
