from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from proactive_mcp.cli.service_unit import render_user_unit
from tests.service_cli_support import (
    ENTRYPOINT,
    PID,
    make_harness,
    parse_response,
    record_running,
    run_cli,
    run_cli_in_process,
)


def test_rendered_unit_restarts_only_retryable_daemon_failures() -> None:
    # Given: an isolated executable and profile database.
    executable = Path("/opt/proactive/bin/proactive-mcp")
    database = Path("/var/lib/proactive/proactive.db")

    # When: the managed user unit is rendered.
    directives = render_user_unit(executable, database).splitlines()

    # Then: systemd retries failures except the permanent exit status.
    assert "Restart=on-failure" in directives
    assert "RestartPreventExitStatus=2" in directives


def test_service_help_exposes_install_status_and_remove() -> None:
    # Given: the installed CLI entry point.

    # When: a user asks for service lifecycle help.
    result = subprocess.run(
        [sys.executable, "-m", "proactive_mcp", "service", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    # Then: all supported lifecycle actions are discoverable.
    assert result.returncode == 0
    assert "install" in result.stdout
    assert "status" in result.stdout
    assert "remove" in result.stdout


def test_absolute_install_does_not_depend_on_path(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.env["PATH"] = "/usr/bin:/bin"
    record_running(harness.database)

    # When: installation uses the in-process manager used by Windows CI.
    result = run_cli_in_process(harness, "install")

    # Then: it preserves the same managed lifecycle response as POSIX CI.
    response = parse_response(result)
    assert result.returncode == 0
    assert response.state == "installed"
    assert response.enabled is True
    assert response.active is True
    assert response.main_pid == PID


def test_install_writes_absolute_managed_restartable_unit(tmp_path: Path) -> None:
    # Given: a fake user systemd manager and a current daemon heartbeat.
    harness = make_harness(tmp_path)
    record_running(harness.database)

    # When: the service is installed through the real CLI process.
    result = run_cli(harness, "install")

    # Then: systemd reports a managed, enabled, active, heartbeat-backed unit.
    response = parse_response(result)
    unit = harness.unit.read_text(encoding="utf-8")
    exec_start = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert result.returncode == 0
    assert response.state == "installed"
    assert response.managed is True
    assert response.enabled is True
    assert response.active is True
    assert response.main_pid == PID
    assert response.heartbeat == "running"
    assert response.linger == "enabled"
    assert response.guidance == "none"
    rendered_executable = (
        exec_start.removeprefix("ExecStart=")
        .removesuffix(" daemon")
        .strip('"')
        .replace("\\\\", "\\")
    )
    assert Path(rendered_executable) == ENTRYPOINT
    assert " daemon" in exec_start
    assert "Restart=on-failure" in unit.splitlines()
    assert "RestartPreventExitStatus=2" in unit.splitlines()
    assert "Type=notify" in unit.splitlines()
    assert "PrivateTmp=true" not in unit.splitlines()
    assert "X-Proactive-MCP-Managed=true" in unit.splitlines()
