import os
from pathlib import Path

from proactive_mcp.server import StatusResponse
from tests.cli_behavior_test_support import run_cli


def test_help_is_useful() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "status" in result.stdout
    assert "setup" in result.stdout
    assert "disconnect" in result.stdout
    assert "google-smoke" in result.stdout
    assert "daemon" in result.stdout


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


def test_invalid_command_prints_usage_and_fails() -> None:
    result = run_cli("definitely-invalid")

    assert result.returncode != 0
    assert "usage:" in result.stderr.lower()
