import os
import subprocess
import sys
from pathlib import Path

import pytest

from proactive_mcp import cli
from proactive_mcp.server import StatusResponse


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


def test_status_prints_server_status_contract(tmp_path: Path) -> None:
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "status.db")}
    result = run_cli("status", env=env)

    assert result.returncode == 0
    status = StatusResponse.model_validate_json(result.stdout)
    assert status.database.status == "healthy"
    assert status.google.gmail == "not_configured"
    assert status.google.calendar == "not_configured"
    assert status.daemon.status == "not_running"
    assert status.overall == "degraded"
    assert status.warnings


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
