import os
import subprocess
import sys
from pathlib import Path

import pytest

from proactive_mcp import cli
from proactive_mcp.server import StatusResponse
from proactive_mcp.sources import (
    GoogleOAuthAuthorizationTimeoutError,
    GoogleSetupOptions,
)


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "proactive_mcp", *args]
    return subprocess.run(command, capture_output=True, text=True, env=env, check=False)


def test_help_is_useful() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "status" in result.stdout
    assert "setup" in result.stdout
    assert "google-smoke" in result.stdout


def test_status_prints_server_status_contract(tmp_path: Path) -> None:
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "status.db")}
    result = run_cli("status", env=env)

    assert result.returncode == 0
    status = StatusResponse.model_validate_json(result.stdout)
    assert status.database.status == "healthy"
    assert status.google.gmail.status == "not_configured"
    assert status.google.gmail.last_success_at is None
    assert status.google.gmail.last_attempt_at is None
    assert status.google.gmail.age_seconds is None
    assert status.google.gmail.error_code is None
    assert status.google.calendar.status == "not_configured"
    assert status.daemon.status == "not_running"
    assert status.overall == "degraded"
    assert status.warnings


def test_setup_help_exposes_reauthorization_and_headless_controls() -> None:
    result = run_cli("setup", "--help")

    assert result.returncode == 0
    assert "--reauth" in result.stdout
    assert "--headless" in result.stdout
    assert "--client-secrets" in result.stdout


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


def test_google_smoke_requires_explicit_real_account_read_opt_in(
    tmp_path: Path,
) -> None:
    # Given: an isolated state directory without credentials.
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "state.db")}

    # When: the smoke command omits its confirmation flag.
    result = run_cli("google-smoke", env=env)

    # Then: it fails before loading credentials or reading Google data.
    assert result.returncode != 0
    assert result.stderr


def test_google_smoke_reports_missing_stored_credentials(tmp_path: Path) -> None:
    # Given: an isolated state directory and a guaranteed unavailable keyring.
    env = os.environ | {
        "PROACTIVE_DATABASE": str(tmp_path / "state.db"),
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
    }

    # When: the explicitly enabled smoke command has no stored credential.
    result = run_cli("google-smoke", "--confirm-real-account-read", env=env)

    # Then: it fails clearly without attempting a network read.
    assert result.returncode != 0
    assert result.stderr


def test_invalid_command_prints_usage_and_fails() -> None:
    result = run_cli("definitely-invalid")

    assert result.returncode != 0
    assert "usage:" in result.stderr.lower()


def test_serve_delegates_to_official_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cli, "run_server", lambda: called.append(True))

    assert cli.main(["serve"]) == 0
    assert called == [True]
