from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pytest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
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
    GoogleOAuthAuthorizationTimeoutError,
    GoogleOAuthAuthorizer,
    OAuthClientConfigError,
    write_headless_authorization_url,
)

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
    authorized = authorizer.authorize(
        FIXTURES / "installed-client.json", headless=True
    )
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
