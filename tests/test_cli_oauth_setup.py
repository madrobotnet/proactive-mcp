import os
import webbrowser
from pathlib import Path

import pytest
from oauthlib.oauth2 import AccessDeniedError
from requests.exceptions import RequestException

from proactive_mcp import cli
from proactive_mcp.sources import (
    GoogleOAuthAuthorizationTimeoutError,
    GoogleSetupOptions,
)
from tests.cli_behavior_test_support import run_cli
from tests.cli_oauth_test_support import (
    FIXTURES,
    ErrorInstalledAppFlow,
)
from tests.cli_oauth_test_support import (
    install_fake_authorizer as _install_fake_authorizer,
)


def test_setup_prefers_explicit_client_secrets_over_environment_and_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: all supported client-secret locations have distinct paths.
    database_path = tmp_path / "state" / "proactive.db"
    explicit_path = tmp_path / "explicit.json"
    environment_path = tmp_path / "environment.json"
    configured: list[tuple[Path, bool, bool]] = []
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(environment_path))

    def configure(path: Path, options: GoogleSetupOptions) -> None:
        assert path == database_path
        configured.append(
            (options.client_secrets_path, options.reauth, options.headless)
        )

    monkeypatch.setattr(cli, "configure_google_sources", configure)

    # When: setup receives an explicit client-secret path.
    result = cli.main(
        [
            "setup",
            "--client-secrets",
            str(explicit_path),
            "--reauth",
            "--headless",
        ]
    )

    # Then: setup selects only the explicit path and forwards its controls.
    assert result == 0
    assert configured == [(explicit_path, True, True)]


def test_setup_reports_a_safe_error_for_invalid_client_secrets(tmp_path: Path) -> None:
    # Given: a nonexistent client-secret path.
    missing_path = tmp_path / "contains-secret.json"
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "state.db")}

    # When: setup attempts to parse the path.
    result = run_cli("setup", "--client-secrets", str(missing_path), env=env)

    # Then: the error is actionable but does not disclose the input path.
    assert result.returncode != 0
    assert result.stderr
    assert str(missing_path) not in result.stderr


def test_setup_reports_authorization_timeout_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the bounded Google loopback flow expires before authorization.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))

    def timeout(_path: Path, _options: GoogleSetupOptions) -> None:
        raise GoogleOAuthAuthorizationTimeoutError

    monkeypatch.setattr(cli, "configure_google_sources", timeout)

    # When: setup reaches the CLI error boundary.
    result = cli.main(["setup", "--client-secrets", str(tmp_path / "client.json")])
    captured = capsys.readouterr()

    # Then: the command fails safely without exposing an exception traceback.
    assert result == 2
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "bootstrap_error",
    [
        OSError("socket-/private/loopback?state=state-canary"),
        webbrowser.Error("browser-/private/provider?code=code-canary"),
    ],
)
def test_setup_reports_bootstrap_failure_as_one_closed_safe_error(
    bootstrap_error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_path = tmp_path / "private-client.json"
    _ = private_path.write_bytes((FIXTURES / "installed-client.json").read_bytes())
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, ErrorInstalledAppFlow(bootstrap_error))

    result = cli.main(["setup", "--client-secrets", str(private_path)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err == "error: Google authorization failed; run setup again\n"
    assert "Traceback" not in captured.err
    assert str(bootstrap_error) not in captured.err
    assert str(private_path) not in captured.err
    assert "state-canary" not in captured.err
    assert "code-canary" not in captured.err


@pytest.mark.parametrize(
    "provider_error",
    [
        AccessDeniedError(
            description="provider-/private/provider?state=state-canary&code=code-canary"
        ),
        RequestException(
            "transport-/private/provider?state=state-canary&code=code-canary"
        ),
    ],
)
def test_setup_reports_provider_failure_as_one_closed_safe_error(
    provider_error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_path = tmp_path / "private-client.json"
    _ = private_path.write_bytes((FIXTURES / "installed-client.json").read_bytes())
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, ErrorInstalledAppFlow(provider_error))

    result = cli.main(["setup", "--client-secrets", str(private_path)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err == "error: Google authorization failed; run setup again\n"
    assert "Traceback" not in captured.err
    assert str(provider_error) not in captured.err
    assert str(private_path) not in captured.err
    assert "state-canary" not in captured.err
    assert "code-canary" not in captured.err


def test_cli_does_not_swallow_process_control_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(_path: Path, _options: GoogleSetupOptions) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "configure_google_sources", interrupted)

    with pytest.raises(KeyboardInterrupt):
        _ = cli.main(["setup", "--client-secrets", "client.json"])
