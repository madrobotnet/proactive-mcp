from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from proactive_mcp.cli import service as service_mod
from proactive_mcp.cli.service_unit import render_user_unit
from tests.service_cli_support import (
    ENTRYPOINT,
    PID,
    FakeStatus,
    make_harness,
    parse_response,
    record_running,
    run_cli,
    run_cli_in_process,
)

if TYPE_CHECKING:
    import pytest


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


def test_execute_service_returns_typed_result_without_presentation_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a Linux layout with a matching heartbeat and no CLI adapter.
    harness = make_harness(tmp_path)
    record_running(harness.database)

    # When: setup-style callers invoke the shared lifecycle core directly.
    with (
        patch.dict(os.environ, harness.env, clear=True),
        patch.object(sys, "platform", "linux"),
        patch.object(service_mod, "_MANAGER", harness.manager),
        patch.object(
            service_mod, "build_status", lambda: FakeStatus(daemon=harness.heartbeat)
        ),
        patch.object(sys, "argv", [str(ENTRYPOINT), "service", "install"]),
    ):
        result = service_mod.execute_service("install")
    captured = capsys.readouterr()

    # Then: the typed result is available without JSON or linger presentation.
    assert captured.out == ""
    assert captured.err == ""
    assert result.success is True
    assert result.response.action == "install"
    assert result.response.state == "installed"


def test_install_emits_english_linger_guidance_without_running_loginctl_enable(
    tmp_path: Path,
) -> None:
    # Given: linger is disabled and the watcher heartbeat matches systemd.
    harness = make_harness(tmp_path, linger="disabled")
    record_running(harness.database)
    user = getpass.getuser()

    # When: the service is installed through the public CLI.
    result = run_cli(harness, "install")

    # Then: JSON keeps enable_linger, stderr has the copy-paste command, no auto-run.
    response = parse_response(result)
    logged = harness.loginctl_log.read_text(encoding="utf-8")
    assert result.returncode == 0
    assert response.linger == "disabled"
    assert response.guidance == "enable_linger"
    if sys.platform.startswith("linux"):
        assert "show-user" in logged
    else:
        assert logged == ""
    assert "enable-linger" not in logged.split()
    assert f"loginctl enable-linger {user}" in result.stderr
