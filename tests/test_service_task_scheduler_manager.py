from __future__ import annotations

import os
from base64 import b64decode
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from proactive_mcp.cli.service_task_scheduler_ready import (
    READY_FILE_ENV,
    signal_task_scheduler_ready,
    task_scheduler_ready_file,
)
from tests.windows_service_support import (
    POWERSHELL,
    RecordingTaskSchedulerRunner,
    load_backend,
    make_harness,
    record_running,
)

if TYPE_CHECKING:
    import pytest


def test_definition_query_handles_absent_task_without_typed_com_catch() -> None:
    backend = load_backend()
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)

    assert manager.definition() is None

    script = b64decode(runner.calls[0][-1], validate=True).decode("utf-16-le")
    assert ".GetTasks(0)" in script
    assert ".GetTask(" not in script


def test_rendered_definition_does_not_claim_byte_encoding_for_com_bstr() -> None:
    backend = load_backend()

    definition = backend.render_task_definition(
        PureWindowsPath(r"C:\Program Files\Proactive\proactive-mcp.exe"),
        PureWindowsPath(r"C:\Users\Ada\proactive.db"),
        POWERSHELL,
    )

    assert "encoding=" not in definition.partition("?>")[0]


def test_start_subscribes_before_run_and_waits_for_ready_file() -> None:
    backend = load_backend()
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)
    ready_file = PureWindowsPath(r"C:\Users\Ada\proactive.db.service-ready")

    assert manager.start(ready_file) is True

    arguments = runner.calls[0]
    script = b64decode(arguments[-1], validate=True).decode("utf-16-le")
    assert str(ready_file) not in " ".join(arguments)
    assert "FileSystemWatcher" in script
    assert "WaitForChanged" in script
    assert script.index("EnableRaisingEvents") < script.index("$task.Run")


def test_main_pid_verifies_requested_heartbeat_pid_is_task_descendant() -> None:
    backend = load_backend()
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)

    assert manager.main_pid(8700) is None

    script = b64decode(runner.calls[0][-1], validate=True).decode("utf-16-le")
    assert "$candidate=8700" in script
    assert "$task.GetInstances(0)" in script
    assert "$roots -contains $cursor" in script


def test_install_passes_profile_ready_file_to_manager(tmp_path: Path) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    record_running(harness.database)

    result = harness.execute(backend, "install")

    assert result.success is True
    assert harness.manager.ready_files == [
        harness.database.with_name(f"{harness.database.name}.service-ready")
    ]


def test_daemon_ready_signal_creates_only_expected_profile_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "profile" / "proactive.db"
    database.parent.mkdir(parents=True)
    ready_file = Path(task_scheduler_ready_file(database))
    monkeypatch.setenv(READY_FILE_ENV, str(ready_file))

    signal_task_scheduler_ready(database)

    assert ready_file.read_text(encoding="ascii") == str(os.getpid())
