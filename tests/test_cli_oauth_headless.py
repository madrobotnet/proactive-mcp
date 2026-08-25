import json
from pathlib import Path

import pytest

from proactive_mcp import cli
from tests.cli_oauth_test_support import (
    FIXTURES,
    FakeInstalledAppFlow,
    TimeoutInstalledAppFlow,
    count_authorization_url_events,
    count_setup_success_events,
    google_credential,
)
from tests.cli_oauth_test_support import (
    install_fake_authorizer as _install_fake_authorizer,
)


def test_headless_setup_emits_no_success_when_authorization_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the injected loopback flow expires before consent.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, TimeoutInstalledAppFlow(google_credential()))

    # When: setup --headless reaches the CLI error boundary.
    result = cli.main(
        [
            "setup",
            "--headless",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ]
    )
    captured = capsys.readouterr()

    # Then: success is absent, URL is at most one, and the error stays safe.
    assert result == 2
    assert count_authorization_url_events(captured.out, captured.err) <= 1
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert captured.err
    assert "Traceback" not in captured.err
    assert str(FIXTURES / "installed-client.json") not in captured.err


def test_headless_setup_emits_no_success_when_client_config_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a malformed client-secret file.
    invalid_path = tmp_path / "client.json"
    _ = invalid_path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    # When: setup --headless parses the untrusted file.
    result = cli.main(["setup", "--headless", "--client-secrets", str(invalid_path)])
    captured = capsys.readouterr()

    # Then: neither a URL nor a success event is emitted.
    assert result == 2
    assert count_authorization_url_events(captured.out, captured.err) == 0
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert str(invalid_path) not in captured.err
    assert "Traceback" not in captured.err


def test_headless_setup_emits_no_success_when_refresh_token_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the flow completes without a durable refresh token.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(
        monkeypatch, FakeInstalledAppFlow(google_credential(refresh_token=None))
    )

    # When: setup --headless tries to persist the credential.
    result = cli.main(
        [
            "setup",
            "--headless",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ]
    )
    captured = capsys.readouterr()

    # Then: failure cannot look like success.
    assert result == 2
    assert count_authorization_url_events(captured.out, captured.err) <= 1
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert "Traceback" not in captured.err


def test_headless_setup_hides_untrusted_client_endpoints_when_authorization_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a valid installed-app shape that points at attacker endpoints.
    client_file = tmp_path / "installed-client.json"
    _ = client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-client.apps.googleusercontent.com",
                    "client_secret": "sanitized-test-client-secret",
                    "auth_uri": "https://accounts.google.com@attacker.invalid/auth",
                    "token_uri": "http://127.0.0.1:8080/token",
                    "redirect_uris": ["https://attacker.invalid/callback"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    # When: setup --headless authorizes with the untrusted file.
    result = cli.main(["setup", "--headless", "--client-secrets", str(client_file)])
    captured = capsys.readouterr()

    # Then: output stays single-owned and does not echo attacker hosts.
    assert result == 0
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 1
    assert "attacker.invalid" not in captured.out
    assert "attacker.invalid" not in captured.err
