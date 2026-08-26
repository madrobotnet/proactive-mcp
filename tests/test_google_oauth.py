from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from google_auth_oauthlib.flow import InstalledAppFlow

from proactive_mcp.sources.credentials import GOOGLE_READONLY_SCOPES, CredentialStore
from proactive_mcp.sources.oauth import (
    HEADLESS_AUTHORIZATION_URL_EVENT,
    GoogleClientConfig,
    GoogleOAuthAuthorizer,
    OAuthClientConfigError,
)
from tests.google_oauth_support import (
    AUTHORIZATION_URL_NEEDLE as _AUTHORIZATION_URL_NEEDLE,
)
from tests.google_oauth_support import (
    FAKE_AUTHORIZATION_URL as _FAKE_AUTHORIZATION_URL,
)
from tests.google_oauth_support import (
    FIXTURES,
    FakeFlowFactory,
    FakeInstalledAppFlow,
    FakeKeyring,
    FlowCall,
    RecordingOauthlibFlow,
    count_authorization_url_events,
    count_setup_success_events,
    google_credential,
)
from tests.google_oauth_support import (
    TEST_BOOTSTRAP_CLIENT_SECRET as _TEST_BOOTSTRAP_CLIENT_SECRET,
)
from tests.google_oauth_support import TEST_CLIENT_ID as _TEST_CLIENT_ID
from tests.google_oauth_support import TEST_CLIENT_SECRET as _TEST_CLIENT_SECRET
from tests.google_oauth_support import TEST_REFRESH as _TEST_REFRESH

if TYPE_CHECKING:
    from pathlib import Path


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
    )
    assert completed.err == ""

    malformed_client = tmp_path / "malformed-client.json"
    _ = malformed_client.write_text("{", encoding="utf-8")
    with pytest.raises(OAuthClientConfigError):
        _ = authorizer.authorize(malformed_client, headless=True)
    failed = capsys.readouterr()

    assert failed.out == ""
    assert failed.err == ""


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


def test_headless_authorization_emits_single_url_without_setup_success(
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

    # Then: the authorizer owns one URL event, but not final setup success.
    assert authorized.refresh_token == _TEST_REFRESH
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 0


def test_headless_reauth_emits_single_url_without_setup_success(
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

    # Then: URL ownership stays singular and final setup success stays external.
    assert authorized.refresh_token == _TEST_REFRESH
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 0


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
    assert count_setup_success_events(after_authorize.out, after_authorize.err) == 0
    assert len(recording.authorization_prompts) == 1
    owned_prompt = recording.authorization_prompts[0]
    assert owned_prompt is not None
    assert owned_prompt.format(url=_FAKE_AUTHORIZATION_URL) == ""
    first_emit = capsys.readouterr()
    assert count_authorization_url_events(first_emit.out, first_emit.err) == 1
    assert owned_prompt.format(url=_FAKE_AUTHORIZATION_URL) == ""
    second_emit = capsys.readouterr()
    assert count_authorization_url_events(second_emit.out, second_emit.err) == 0
