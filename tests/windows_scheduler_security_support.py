from __future__ import annotations

import importlib
from base64 import b64decode, b64encode
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from tests.windows_service_support import (
    RecordingTaskSchedulerRunner,
    TaskSchedulerBackend,
)

if TYPE_CHECKING:
    import pytest


def assert_native_system_directory(
    backend: TaskSchedulerBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager_module = importlib.import_module(
        "proactive_mcp.cli.service_task_scheduler_manager"
    )
    powershell = PureWindowsPath(
        r"D:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )

    def trusted_path(platform: str) -> str:
        assert platform == "win32"
        return str(powershell)

    monkeypatch.setattr(manager_module, "trusted_notifier_path", trusted_path)
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)

    _ = manager.definition()

    assert runner.calls[0][0] == str(powershell)


def assert_register_embeds_definition_in_encoded_command(
    backend: TaskSchedulerBackend,
) -> None:
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)
    definition = "<Task><RegistrationInfo /></Task>"

    assert manager.register(definition) is True

    arguments = runner.calls[0]
    assert "-EncodedArguments" not in arguments
    assert len(arguments) == 6
    script = b64decode(arguments[-1], validate=True).decode("utf-16-le")
    definition_token = b64encode(definition.encode()).decode()
    assert f"FromBase64String('{definition_token}')" in script
    assert definition not in " ".join(arguments)
