from __future__ import annotations

import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib import flow as oauthlib_flow
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from oauthlib.oauth2 import AccessDeniedError
from requests.exceptions import RequestException
from requests_oauthlib import oauth2_session
from typing_extensions import override

from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStore,
    GoogleCredential,
    MissingRefreshTokenError,
)
from proactive_mcp.sources.oauth import (
    HEADLESS_AUTHORIZATION_URL_EVENT,
    HEADLESS_SETUP_SUCCESS_EVENT,
    GoogleClientConfig,
    GoogleOAuthAuthorizationError,
    GoogleOAuthAuthorizationTimeoutError,
    GoogleOAuthAuthorizer,
    OAuthClientConfigError,
    write_headless_authorization_url,
)

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse, WSGIEnvironment

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "auth"
_TEST_ACCESS = "access" + "-token"
_TEST_REFRESH = "refresh" + "-token"
_TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
_TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client" + "-secret"
_TEST_BOOTSTRAP_CLIENT_SECRET = "sanitized-test-client" + "-secret"
_FAKE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/auth"
    "?client_id=test-client.apps.googleusercontent.com"
    "&redirect_uri=http%3A%2F%2F127.0.0.1"
)
_AUTHORIZATION_URL_NEEDLE = "https://accounts.google.com/o/oauth2/auth"


def count_authorization_url_events(*parts: str) -> int:
    count = 0
    for text in parts:
        for line in text.splitlines():
            if line.startswith(HEADLESS_AUTHORIZATION_URL_EVENT) or (
                _AUTHORIZATION_URL_NEEDLE in line
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


@dataclass(slots=True)
class CallbackCanaryApplication:
    callback_received: threading.Event = field(default_factory=threading.Event)
    request_target: str | None = None

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


def test_oauth_owned_output_and_typed_failure_characterization(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(FakeInstalledAppFlow(google_credential())),
    )

    authorized = authorizer.authorize(FIXTURES / "installed-client.json", headless=True)
    completed = capsys.readouterr()

    assert authorized.refresh_token == _TEST_REFRESH
    assert store.load() is not None
    assert completed.out == (
        f"{HEADLESS_AUTHORIZATION_URL_EVENT} {_FAKE_AUTHORIZATION_URL}\n"
        f"{HEADLESS_SETUP_SUCCESS_EVENT}\n"
    )
    assert completed.err == ""

    malformed_client = tmp_path / "malformed-client.json"
    _ = malformed_client.write_text("{", encoding="utf-8")
    with pytest.raises(OAuthClientConfigError):
        _ = authorizer.authorize(malformed_client, headless=True)
    failed = capsys.readouterr()

    assert failed.out == ""
    assert failed.err == ""


@pytest.mark.parametrize(
    ("request_target", "canaries"),
    [
        (
            "/?code=oauth-code-canary&state=oauth-state-canary",
            ("oauth-code-canary", "oauth-state-canary"),
        ),
        (
            "/?error=access_denied&error_description=malformed-%ZZ-canary",
            ("malformed-%ZZ-canary",),
        ),
    ],
)
def test_loopback_callback_access_log_hides_oauth_query_canaries(
    request_target: str,
    canaries: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = (
        f"GET {request_target} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    ).encode()
    app = CallbackCanaryApplication()
    handler_class = cast(
        "type[WSGIRequestHandler]",
        vars(oauthlib_flow)["_WSGIRequestHandler"],
    )
    server = make_server("127.0.0.1", 0, app, handler_class=handler_class)
    server.timeout = 2
    handler_started = threading.Event()
    handler_stopped = threading.Event()

    def handle_callback() -> None:
        handler_started.set()
        try:
            server.handle_request()
        finally:
            handler_stopped.set()

    caplog.set_level(logging.INFO, logger="google_auth_oauthlib.flow")
    thread = threading.Thread(target=handle_callback, name="oauth-callback-canary")
    thread.start()
    response = b""
    try:
        assert handler_started.wait(timeout=2)
        with socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=2
        ) as callback:
            callback.settimeout(2)
            callback.sendall(request)
            while chunk := callback.recv(4096):
                response += chunk
        assert app.callback_received.wait(timeout=2)
        assert handler_stopped.wait(timeout=2)
    finally:
        server.server_close()
        thread.join(timeout=3)

    assert not thread.is_alive()
    logging.getLogger("google_auth_oauthlib.flow").info("oauth.non_access_canary")
    captured = capsys.readouterr()
    combined_output = "\n".join(
        (
            captured.out,
            captured.err,
            *(record.getMessage() for record in caplog.records),
        )
    )
    assert app.request_target == request_target
    assert b"200 OK" in response
    assert b"callback complete" in response
    assert "oauth.non_access_canary" in combined_output
    assert all(canary not in combined_output for canary in canaries)


def test_requests_oauthlib_debug_credentials_are_fenced_at_the_exact_source(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canaries = (
        "oauth-state-canary",
        "oauth-code-canary",
        "client-secret-canary",
        "access-token-canary",
        "refresh-token-canary",
        "/private/oauth-path-canary",
    )
    logger = logging.getLogger("requests_oauthlib.oauth2_session")
    source = oauth2_session.__file__
    assert source is not None
    caplog.set_level(logging.DEBUG)

    for canary in canaries:
        record = logger.makeRecord(
            logger.name,
            logging.DEBUG,
            source,
            1,
            "upstream OAuth diagnostic %s",
            (canary,),
            None,
            "fetch_token",
        )
        logger.handle(record)

    for level, message in (
        (logging.INFO, "oauth-info-diagnostic-canary"),
        (logging.WARNING, "oauth-warning-diagnostic-canary"),
    ):
        record = logger.makeRecord(
            logger.name,
            level,
            source,
            1,
            message,
            (),
            None,
            "fetch_token",
        )
        logger.handle(record)

    captured = capsys.readouterr()
    combined_output = "\n".join(
        (
            captured.out,
            captured.err,
            *(record.getMessage() for record in caplog.records),
        )
    )
    assert all(combined_output.count(canary) == 0 for canary in canaries)
    assert "oauth-info-diagnostic-canary" in combined_output
    assert "oauth-warning-diagnostic-canary" in combined_output


def test_oauthlib_same_template_non_callback_log_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="google_auth_oauthlib.flow")

    logging.getLogger("google_auth_oauthlib.flow").info(
        '"%s" %s %s',
        "generic-non-callback-canary",
        201,
        12,
    )

    assert any(
        record.getMessage() == '"generic-non-callback-canary" 201 12'
        for record in caplog.records
    )


def test_reauth_preserves_stale_credentials_until_consent_succeeds(
    tmp_path: Path,
) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path / "state", keyring=keyring)
    store.save(google_credential())
    flow = FakeInstalledAppFlow(google_credential())
    factory = FakeFlowFactory(flow)
    authorizer = GoogleOAuthAuthorizer(store, flow_factory=factory)

    authorized = authorizer.authorize(
        FIXTURES / "installed-client.json", reauth=True, headless=True
    )

    assert authorized.refresh_token == _TEST_REFRESH
    assert keyring.calls == ["set", "set"]
    assert factory.client_config == {
        "installed": {
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "client_id": _TEST_CLIENT_ID,
            "client_secret": _TEST_BOOTSTRAP_CLIENT_SECRET,
            "redirect_uris": ["http://127.0.0.1"],
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
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


def test_authorization_ignores_untrusted_provider_endpoint_overrides(
    tmp_path: Path,
) -> None:
    client_file = tmp_path / "installed-client.json"
    _ = client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "auth_uri": "https://accounts.google.com@attacker.invalid/auth",
                    "client_id": _TEST_CLIENT_ID,
                    "client_secret": _TEST_CLIENT_SECRET,
                    "redirect_uris": ["https://attacker.invalid/callback"],
                    "token_uri": "http://127.0.0.1:8080/token",
                }
            }
        ),
        encoding="utf-8",
    )
    factory = FakeFlowFactory(FakeInstalledAppFlow(google_credential()))
    authorizer = GoogleOAuthAuthorizer(
        CredentialStore(tmp_path / "state", keyring=FakeKeyring()),
        flow_factory=factory,
    )

    _ = authorizer.authorize(client_file, headless=True)

    assert factory.client_config == {
        "installed": {
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "client_id": _TEST_CLIENT_ID,
            "client_secret": _TEST_CLIENT_SECRET,
            "redirect_uris": ["http://127.0.0.1"],
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def test_authorization_rejects_a_flow_without_a_refresh_token(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(
            FakeInstalledAppFlow(google_credential(refresh_token=None))
        ),
    )

    with pytest.raises(MissingRefreshTokenError):
        _ = authorizer.authorize(FIXTURES / "installed-client.json")

    assert store.load() is None


@pytest.mark.parametrize(
    "provider_error",
    [
        AccessDeniedError(description="provider-/private/path?state=state&code=code"),
        RequestException("transport-/private/path?state=state&code=code"),
    ],
)
def test_authorization_provider_failures_return_one_safe_typed_error(
    provider_error: Exception,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(ErrorInstalledAppFlow(provider_error)),
    )

    with pytest.raises(GoogleOAuthAuthorizationError) as error:
        _ = authorizer.authorize(FIXTURES / "installed-client.json", headless=True)
    captured = capsys.readouterr()

    assert str(error.value) == "Google authorization failed; run setup again"
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert captured.out == ""
    assert captured.err == ""
    assert store.load() is None


@pytest.mark.parametrize("control", [KeyboardInterrupt(), SystemExit(19)])
def test_authorization_does_not_swallow_process_control_exceptions(
    control: BaseException,
    tmp_path: Path,
) -> None:
    authorizer = GoogleOAuthAuthorizer(
        CredentialStore(tmp_path / "state", keyring=FakeKeyring()),
        flow_factory=FakeFlowFactory(ErrorInstalledAppFlow(control)),
    )

    with pytest.raises(type(control)) as raised:
        _ = authorizer.authorize(FIXTURES / "installed-client.json")

    assert raised.value is control


def test_authorization_timeout_returns_a_typed_error(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(TimeoutInstalledAppFlow(google_credential())),
    )

    with pytest.raises(GoogleOAuthAuthorizationTimeoutError) as error:
        _ = authorizer.authorize(
            FIXTURES / "installed-client.json",
            headless=True,
        )

    assert isinstance(error.value, GoogleOAuthAuthorizationTimeoutError)


def test_headless_authorization_emits_single_url_and_success_when_consent_completes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a headless loopback flow that mimics oauthlib print+log presentation.
    authorizer = GoogleOAuthAuthorizer(
        CredentialStore(tmp_path / "state", keyring=FakeKeyring()),
        flow_factory=FakeFlowFactory(FakeInstalledAppFlow(google_credential())),
    )

    # When: authorization completes and the credential is persisted.
    authorized = authorizer.authorize(FIXTURES / "installed-client.json", headless=True)
    captured = capsys.readouterr()

    # Then: exactly one URL event and one success event are owned.
    assert authorized.refresh_token == _TEST_REFRESH
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 1


def test_headless_reauth_emits_single_url_and_success_when_consent_completes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an existing stored credential that must be replaced.
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    store.save(google_credential())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(FakeInstalledAppFlow(google_credential())),
    )

    # When: headless reauthorization completes.
    authorized = authorizer.authorize(
        FIXTURES / "installed-client.json", reauth=True, headless=True
    )
    captured = capsys.readouterr()

    # Then: output ownership stays one URL and one success.
    assert authorized.refresh_token == _TEST_REFRESH
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 1


def test_headless_timeout_emits_no_success_when_loopback_expires(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the bounded loopback server expires before consent.
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(TimeoutInstalledAppFlow(google_credential())),
    )

    # When: headless authorization times out.
    with pytest.raises(GoogleOAuthAuthorizationTimeoutError):
        _ = authorizer.authorize(
            FIXTURES / "installed-client.json",
            headless=True,
        )
    captured = capsys.readouterr()

    # Then: at most one URL event is visible and success is absent.
    assert count_authorization_url_events(captured.out, captured.err) <= 1
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert store.load() is None


def test_headless_invalid_config_emits_no_success_when_client_json_is_untrusted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an untrusted client file that is not valid installed-app JSON.
    client_file = tmp_path / "installed-client.json"
    _ = client_file.write_text("{", encoding="utf-8")
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(FakeInstalledAppFlow(google_credential())),
    )

    # When: setup parses the invalid client configuration.
    with pytest.raises(OAuthClientConfigError):
        _ = authorizer.authorize(client_file, headless=True)
    captured = capsys.readouterr()

    # Then: no URL or success event is emitted and no attacker host leaks.
    assert count_authorization_url_events(captured.out, captured.err) == 0
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert "attacker.invalid" not in captured.out
    assert "attacker.invalid" not in captured.err
    assert store.load() is None


def test_headless_credential_failure_emits_no_success_when_refresh_token_is_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a completed loopback flow that did not issue a refresh token.
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(
            FakeInstalledAppFlow(google_credential(refresh_token=None))
        ),
    )

    # When: persistence rejects the non-durable credential.
    with pytest.raises(MissingRefreshTokenError):
        _ = authorizer.authorize(
            FIXTURES / "installed-client.json",
            headless=True,
        )
    captured = capsys.readouterr()

    # Then: a URL may have been shown, but success is never emitted.
    assert count_authorization_url_events(captured.out, captured.err) <= 1
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert store.load() is None


def test_browser_authorization_prompt_never_emits_state_bearing_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = RecordingOauthlibFlow(google_credential())

    def from_client_config(
        _cls: type[InstalledAppFlow],
        _client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> RecordingOauthlibFlow:
        del scopes
        return recording

    monkeypatch.setattr(
        InstalledAppFlow,
        "from_client_config",
        classmethod(from_client_config),
    )

    _ = GoogleOAuthAuthorizer(
        CredentialStore(tmp_path / "state", keyring=FakeKeyring()),
    ).authorize(FIXTURES / "installed-client.json", headless=False)
    _ = capsys.readouterr()
    owned_prompt = recording.authorization_prompts[0]
    assert owned_prompt is not None
    assert (
        owned_prompt.format(url=f"{_FAKE_AUTHORIZATION_URL}&state=browser-state-canary")
        == ""
    )
    captured = capsys.readouterr()

    assert captured.out == "Waiting for Google authorization in your browser.\n"
    assert captured.err == ""
    assert "browser-state-canary" not in captured.out
    assert _AUTHORIZATION_URL_NEEDLE not in captured.out


def test_headless_real_adapter_supplies_owned_authorization_prompt_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the real installed-app factory wrapping a recording oauthlib flow.
    recording = RecordingOauthlibFlow(google_credential())

    def from_client_config(
        _cls: type[InstalledAppFlow],
        _client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> RecordingOauthlibFlow:
        del scopes
        return recording

    monkeypatch.setattr(
        InstalledAppFlow,
        "from_client_config",
        classmethod(from_client_config),
    )

    # When: authorize uses _GoogleInstalledAppFlowFactory / local adapter.
    authorized = GoogleOAuthAuthorizer(
        CredentialStore(tmp_path / "state", keyring=FakeKeyring()),
    ).authorize(FIXTURES / "installed-client.json", headless=True)
    after_authorize = capsys.readouterr()

    # Then: the adapter supplied exactly one owned authorization prompt.
    assert authorized.refresh_token == _TEST_REFRESH
    assert count_setup_success_events(after_authorize.out, after_authorize.err) == 1
    assert len(recording.authorization_prompts) == 1
    owned_prompt = recording.authorization_prompts[0]
    assert owned_prompt is not None
    assert owned_prompt.format(url=_FAKE_AUTHORIZATION_URL) == ""
    first_emit = capsys.readouterr()
    assert count_authorization_url_events(first_emit.out, first_emit.err) == 1
    assert owned_prompt.format(url=_FAKE_AUTHORIZATION_URL) == ""
    second_emit = capsys.readouterr()
    assert count_authorization_url_events(second_emit.out, second_emit.err) == 0
