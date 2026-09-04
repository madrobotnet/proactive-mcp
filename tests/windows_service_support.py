from __future__ import annotations

import hashlib
import importlib
import os
import sys
from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import TYPE_CHECKING, Final, Protocol, final, runtime_checkable
from unittest.mock import patch
from xml.etree import ElementTree as ET

from proactive_mcp.delivery.notify import trusted_notifier_path
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence

    from proactive_mcp.cli.service_models import ServiceAction, ServiceCommandResult

PID: Final = os.getpid()
POWERSHELL: Final = PureWindowsPath(trusted_notifier_path("win32"))
_BACKEND_PATH: Final = (
    Path(__file__).parents[1]
    / "src"
    / "proactive_mcp"
    / "cli"
    / "service_task_scheduler.py"
)


class TaskSchedulerManager(Protocol):
    def definition(self) -> str | None: ...

    def is_enabled(self) -> bool: ...

    def is_active(self) -> bool: ...

    def main_pid(self, expected_pid: int | None = None) -> int | None: ...

    def register(self, definition: str) -> bool: ...

    def start(self, ready_file: PurePath | None = None) -> bool: ...

    def stop(self) -> bool: ...

    def delete(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class TaskSchedulerRunResult:
    succeeded: bool
    output: str


class TaskSchedulerCommandRunner(Protocol):
    def run(self, argv: Sequence[str]) -> TaskSchedulerRunResult: ...


class WindowsTaskSchedulerManagerFactory(Protocol):
    def __call__(
        self,
        *,
        runner: TaskSchedulerCommandRunner,
    ) -> TaskSchedulerManager: ...


@runtime_checkable
class TaskSchedulerBackend(Protocol):
    TASK_NAME: str
    MANAGED_TASK_MARKER: str
    WindowsTaskSchedulerManager: WindowsTaskSchedulerManagerFactory

    def render_task_definition(
        self,
        executable: PurePath,
        database: PurePath,
        powershell: PurePath,
    ) -> str: ...

    def is_managed_task(self, definition: str) -> bool: ...

    def execute_task_scheduler(
        self,
        action: ServiceAction,
        manager: TaskSchedulerManager | None = None,
    ) -> ServiceCommandResult: ...


@final
class RecordingTaskSchedulerRunner:
    """Record exact process argument vectors without invoking Windows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str]) -> TaskSchedulerRunResult:
        self.calls.append(tuple(argv))
        return TaskSchedulerRunResult(succeeded=True, output="")


@final
class FakeTaskSchedulerManager:
    """Mutable state machine standing in for the Windows scheduler boundary."""

    __slots__: tuple[str, ...] = (
        "active",
        "enabled",
        "fail_register_once",
        "fail_start_once",
        "main_pid_requests",
        "main_pid_value",
        "operations",
        "ready_files",
        "stored_definition",
    )

    def __init__(self) -> None:
        self.stored_definition: str | None = None
        self.enabled: bool = False
        self.active: bool = False
        self.main_pid_value: int | None = PID
        self.main_pid_requests: list[int | None] = []
        self.fail_register_once: bool = False
        self.fail_start_once: bool = False
        self.operations: list[str] = []
        self.ready_files: list[PurePath | None] = []

    def definition(self) -> str | None:
        return self.stored_definition

    def is_enabled(self) -> bool:
        return self.enabled

    def is_active(self) -> bool:
        return self.active

    def main_pid(self, expected_pid: int | None = None) -> int | None:
        self.main_pid_requests.append(expected_pid)
        if not self.active or (
            expected_pid is not None and expected_pid != self.main_pid_value
        ):
            return None
        return self.main_pid_value

    def register(self, definition: str) -> bool:
        self.operations.append("register")
        if self.fail_register_once:
            self.fail_register_once = False
            return False
        self.stored_definition = definition
        self.enabled = True
        return True

    def start(self, ready_file: PurePath | None = None) -> bool:
        self.operations.append("start")
        self.ready_files.append(ready_file)
        if self.fail_start_once:
            self.fail_start_once = False
            return False
        self.active = True
        return True

    def stop(self) -> bool:
        self.operations.append("stop")
        self.active = False
        return True

    def delete(self) -> bool:
        self.operations.append("delete")
        self.stored_definition = None
        self.enabled = False
        self.active = False
        return True


@dataclass(frozen=True, slots=True)
class WindowsServiceHarness:
    executable: Path
    database: Path
    manager: FakeTaskSchedulerManager

    def execute(
        self,
        backend: TaskSchedulerBackend,
        action: ServiceAction,
    ) -> ServiceCommandResult:
        environment = {
            "PROACTIVE_DATABASE": str(self.database),
            "SystemRoot": r"C:\Windows",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch.object(sys, "argv", [str(self.executable), "service", action]),
        ):
            return backend.execute_task_scheduler(action, self.manager)


def load_backend() -> TaskSchedulerBackend:
    assert _BACKEND_PATH.exists(), (
        "Issue #45 backend is not implemented: "
        "src/proactive_mcp/cli/service_task_scheduler.py is missing"
    )
    module = importlib.import_module("proactive_mcp.cli.service_task_scheduler")
    if not isinstance(module, TaskSchedulerBackend):
        msg = "Issue #45 backend does not expose the required typed surface"
        raise TypeError(msg)
    return module


def make_harness(tmp_path: Path) -> WindowsServiceHarness:
    executable = tmp_path / "proactive-mcp.exe"
    _ = executable.write_text("test executable", encoding="utf-8")
    executable.chmod(0o700)
    return WindowsServiceHarness(
        executable=executable,
        database=tmp_path / "profile" / "proactive.db",
        manager=FakeTaskSchedulerManager(),
    )


def record_running(database: Path, *, pid: int = PID) -> None:
    with Store(database) as store:
        store.daemon.record_start(pid)


def xml_text(definition: str, local_name: str) -> str:
    matches = [
        element.text
        for element in ET.fromstring(definition).iter()  # noqa: S314
        if element.tag.rpartition("}")[2] == local_name
    ]
    assert len(matches) == 1
    value = matches[0]
    assert value is not None
    return value


def xml_count(definition: str, local_name: str) -> int:
    return sum(
        element.tag.rpartition("}")[2] == local_name
        for element in ET.fromstring(definition).iter()  # noqa: S314
    )


def assert_encoded_launcher(backend: TaskSchedulerBackend) -> None:
    executable = PureWindowsPath(
        "C:\\Users\\O'Brien\\$(Remove-Item canary)\\proactive-mcp.exe"
    )
    database = PureWindowsPath("C:\\Users\\O'Brien\\state; Write-Output injected.db")
    definition = backend.render_task_definition(executable, database, POWERSHELL)
    arguments = tuple(xml_text(definition, "Arguments").split())
    launcher = b64decode(arguments[-1], validate=True).decode("utf-16-le")
    assert len(arguments) == 5
    assert arguments[:4] == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
    )
    assert str(executable) not in definition
    assert str(database) not in definition
    assert str(executable) not in launcher
    assert str(database) not in launcher
    executable_token = b64encode(str(executable).encode()).decode()
    database_token = b64encode(str(database).encode()).decode()
    ready_file = database.with_name(f"{database.name}.service-ready")
    ready_token = b64encode(str(ready_file).encode()).decode()
    assert f"FromBase64String('{executable_token}')" in launcher
    assert f"FromBase64String('{database_token}')" in launcher
    assert f"FromBase64String('{ready_token}')" in launcher
    assert "$env:PROACTIVE_DATABASE = $database" in launcher
    assert "$env:PROACTIVE_SERVICE_READY_FILE = $readyFile" in launcher
    assert launcher.index("[IO.File]::Delete($readyFile)") < launcher.index(
        "& $executable 'daemon'"
    )
    invocations = [
        line.strip() for line in launcher.splitlines() if line.lstrip().startswith("&")
    ]
    assert invocations == ["& $executable 'daemon'"]
    forbidden = ("Start-Process", "Invoke-Expression", "cmd.exe", "shell=True")
    assert not any(value.casefold() in launcher.casefold() for value in forbidden)


def assert_trusted_manager_invocation(backend: TaskSchedulerBackend) -> None:
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)
    assert manager.definition() is None
    assert len(runner.calls) == 1
    arguments = runner.calls[0]
    assert isinstance(arguments, tuple)
    assert arguments[0] == str(POWERSHELL)
    assert arguments[1:5] == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
    )
    assert len(arguments) == 6
    _ = b64decode(arguments[5], validate=True)
    assert not any(
        forbidden.casefold() in " ".join(arguments).casefold()
        for forbidden in ("cmd.exe", " /TR ", " -Command ")
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
