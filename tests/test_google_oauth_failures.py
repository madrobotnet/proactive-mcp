from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

import pytest
from oauthlib.oauth2 import AccessDeniedError
from requests.exceptions import RequestException

from proactive_mcp.sources.credentials import (
    CredentialStore,
    MissingRefreshTokenError,
)
from proactive_mcp.sources.oauth import (
    GoogleOAuthAuthorizationError,
    GoogleOAuthAuthorizationTimeoutError,
    GoogleOAuthAuthorizer,
    OAuthClientConfigError,
)
from tests.google_oauth_support import (
    FIXTURES,
    ErrorInstalledAppFlow,
    FakeFlowFactory,
    FakeInstalledAppFlow,
    FakeKeyring,
    TimeoutInstalledAppFlow,
    count_authorization_url_events,
    count_setup_success_events,
    google_credential,
)

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.parametrize(
    "bootstrap_error",
    [
        OSError("socket-/private/loopback?state=state-canary"),
        webbrowser.Error("browser-/private/provider?code=code-canary"),
    ],
)
def test_authorization_bootstrap_failures_return_one_safe_typed_error(
    bootstrap_error: Exception,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring())
    authorizer = GoogleOAuthAuthorizer(
        store,
        flow_factory=FakeFlowFactory(ErrorInstalledAppFlow(bootstrap_error)),
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
