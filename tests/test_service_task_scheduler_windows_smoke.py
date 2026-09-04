from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import pytest

from proactive_mcp.cli.service_models import ServiceResponse
from proactive_mcp.cli.service_task_scheduler import (
    WindowsTaskSchedulerManager,
    is_managed_task,
    render_task_definition,
)
from proactive_mcp.delivery.notify import trusted_notifier_path
from proactive_mcp.server import build_status


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires the real Windows Task Scheduler service",
)
def test_windows_fresh_install_smoke_matches_scheduler_and_heartbeat_pid(
    tmp_path: Path,
) -> None:
    entrypoint = Path(sys.executable).with_name("proactive-mcp.exe")
    database = tmp_path / "profile" / "proactive.db"
    environment = os.environ | {"PROACTIVE_DATABASE": str(database)}

    try:
        install = subprocess.run(
            [entrypoint, "service", "install"],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
            timeout=90,
        )
        installed = ServiceResponse.model_validate_json(install.stdout)
        assert install.returncode == 0
        assert installed.state == "installed"
        assert installed.enabled is True
        assert installed.active is True
        assert installed.heartbeat == "running"
        assert installed.main_pid is not None

        status = subprocess.run(
            [entrypoint, "service", "status"],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
            timeout=30,
        )
        observed = ServiceResponse.model_validate_json(status.stdout)
        with patch.dict(os.environ, environment, clear=True):
            heartbeat = build_status().daemon
        assert status.returncode == 0
        assert observed.state == "active"
        assert observed.main_pid == installed.main_pid == heartbeat.pid
        assert observed.heartbeat == heartbeat.liveness == "running"
        assert observed.linger == "not_applicable"
        assert observed.guidance == "none"
    finally:
        remove = subprocess.run(
            [entrypoint, "service", "remove"],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
            timeout=30,
        )

    removed = ServiceResponse.model_validate_json(remove.stdout)
    assert remove.returncode == 0
    assert removed.state in {"removed", "absent"}
    assert database.exists()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires the real Windows Task Scheduler service",
)
def test_windows_manager_registers_reads_and_starts_real_task(tmp_path: Path) -> None:
    entrypoint = Path(sys.executable).with_name("proactive-mcp.exe")
    database = tmp_path / "profile" / "proactive.db"
    powershell = PureWindowsPath(trusted_notifier_path("win32"))
    definition = render_task_definition(entrypoint, database, powershell)
    manager = WindowsTaskSchedulerManager()
    created = False

    assert manager.definition() is None
    try:
        assert manager.register(definition) is True
        created = True
        observed = manager.definition()
        assert observed is not None
        assert is_managed_task(observed) is True
        assert manager.start() is True
    finally:
        if created:
            _ = manager.stop()
            _ = manager.delete()
