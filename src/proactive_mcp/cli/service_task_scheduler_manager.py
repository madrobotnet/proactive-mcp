"""PowerShell/COM adapter for the current-user Windows Task Scheduler."""

from __future__ import annotations

import os
import subprocess
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import PurePath

from proactive_mcp.cli.service_task_scheduler_contract import TASK_NAME
from proactive_mcp.delivery.notify import trusted_notifier_path

_TIMEOUT_SECONDS: Final = 35
_PREFIX: Final = (
    "$ErrorActionPreference='Stop'\n"
    "$service=New-Object -ComObject 'Schedule.Service'\n"
    "$service.Connect()\n"
    "$folder=$service.GetFolder('\\')\n"
)
_GET_TASK: Final = f"$task=$folder.GetTask('{TASK_NAME}')\n"
_DEFINITION_SCRIPT: Final = (
    _PREFIX
    + "$task=$null\n"
    + "foreach($candidate in $folder.GetTasks(0)){\n"
    + f"  if($candidate.Name -ceq '{TASK_NAME}'){{$task=$candidate;break}}\n"
    + "}\n"
    + "if($null -eq $task){exit 0}\n"
    + "[Console]::Out.Write($task.Xml)\n"
)
_ENABLED_SCRIPT: Final = (
    _PREFIX
    + _GET_TASK
    + "$enabled=$task.Enabled -and $task.Definition.Settings.Enabled "
    + "-and $task.State -ne 1\n"
    + "if($enabled){[Console]::Out.Write('1')}else{[Console]::Out.Write('0')}\n"
)
_ACTIVE_SCRIPT: Final = (
    _PREFIX
    + _GET_TASK
    + "if($task.State -eq 4){[Console]::Out.Write('1')}"
    + "else{[Console]::Out.Write('0')}\n"
)
_REGISTER_SCRIPT_PREFIX: Final = (
    _PREFIX + "$xml=[Text.Encoding]::UTF8.GetString(" + "[Convert]::FromBase64String('"
)
_REGISTER_SCRIPT_SUFFIX: Final = (
    "'))\n"
    "$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name\n"
    f"$null=$folder.RegisterTask('{TASK_NAME}',$xml,6,$user,$null,3,$null)\n"
)
_START_SCRIPT: Final = _PREFIX + _GET_TASK + "$null=$task.Run($null)\n"
_READY_START_SCRIPT_SUFFIX: Final = f"""'))
$directory=[IO.Path]::GetDirectoryName($ready)
$filename=[IO.Path]::GetFileName($ready)
$watcher=[IO.FileSystemWatcher]::new($directory,$filename)
$watcher.EnableRaisingEvents=$true
try{{
{_GET_TASK}  $null=$task.Run($null)
  if([IO.File]::Exists($ready)){{exit 0}}
  $change=$watcher.WaitForChanged([IO.WatcherChangeTypes]::Created,30000)
  if($change.TimedOut){{exit 1}}
}}finally{{$watcher.Dispose()}}
"""
_STOP_SCRIPT: Final = _PREFIX + _GET_TASK + "$task.Stop(0)\n"
_DELETE_SCRIPT: Final = _PREFIX + f"$folder.DeleteTask('{TASK_NAME}',0)\n"
_MAIN_PID_SCRIPT: Final = (
    _PREFIX
    + _GET_TASK
    + "$expected=[Text.Encoding]::UTF8.GetString("
    + "[Convert]::FromBase64String($task.Definition.Data))\n"
    + "$roots=@($task.GetInstances(0)|ForEach-Object {[int]$_.EnginePID})\n"
    + "if($roots.Count -eq 0){exit 0}\n"
    + "$processes=@(Get-CimInstance Win32_Process)\n"
    + "$parents=@{}\n"
    + "foreach($process in $processes){$parents[[int]$process.ProcessId]="
    + "[int]$process.ParentProcessId}\n"
    + "$matches=@()\n"
    + "foreach($process in $processes){\n"
    + "  if(-not [String]::Equals($process.ExecutablePath,$expected,"
    + "[StringComparison]::OrdinalIgnoreCase)){continue}\n"
    + "  $candidate=[int]$process.ProcessId\n"
    + "  $cursor=$candidate\n"
    + "  while($parents.ContainsKey($cursor)){\n"
    + "    if($roots -contains $cursor){$matches+=$candidate;break}\n"
    + "    $next=[int]$parents[$cursor]\n"
    + "    if($next -eq $cursor){break}\n"
    + "    $cursor=$next\n"
    + "  }\n"
    + "}\n"
    + "if($matches.Count -eq 1){[Console]::Out.Write($matches[0])}\n"
)


class _RunResult(Protocol):
    @property
    def succeeded(self) -> bool: ...

    @property
    def output(self) -> str: ...


class TaskSchedulerCommandRunner(Protocol):
    """Run one trusted PowerShell argument vector."""

    def run(self, argv: Sequence[str]) -> _RunResult:
        """Execute argv and return redacted success plus stdout."""
        ...


@dataclass(frozen=True, slots=True)
class _CommandResult:
    succeeded: bool
    output: str


@dataclass(frozen=True, slots=True)
class _PowerShellCommandRunner:
    """Execute trusted PowerShell without inheriting injection controls."""

    def run(self, argv: Sequence[str]) -> _CommandResult:
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                check=False,
                env=_sanitized_environment(),
                timeout=_TIMEOUT_SECONDS,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _CommandResult(succeeded=False, output="")
        return _CommandResult(completed.returncode == 0, completed.stdout)


@dataclass(frozen=True, slots=True)
class WindowsTaskSchedulerManager:
    """Expose fixed current-user Task Scheduler COM operations."""

    runner: TaskSchedulerCommandRunner = field(default_factory=_PowerShellCommandRunner)

    def definition(self) -> str | None:
        """Export the current task definition."""
        result = self._run(_DEFINITION_SCRIPT)
        if not result.succeeded:
            raise OSError
        return result.output or None

    def is_enabled(self) -> bool:
        """Return whether the registered task is enabled."""
        return self._run(_ENABLED_SCRIPT).output.strip() == "1"

    def is_active(self) -> bool:
        """Return whether the registered task is running."""
        return self._run(_ACTIVE_SCRIPT).output.strip() == "1"

    def main_pid(self, expected_pid: int | None = None) -> int | None:
        """Verify the heartbeat owner belongs to the scheduler process tree."""
        result = self._run(_main_pid_script(expected_pid))
        value = result.output.strip()
        return int(value) if result.succeeded and value.isdecimal() else None

    def register(self, definition: str) -> bool:
        """Register XML with current-user InteractiveToken credentials."""
        definition_token = b64encode(definition.encode()).decode("ascii")
        script = _REGISTER_SCRIPT_PREFIX + definition_token + _REGISTER_SCRIPT_SUFFIX
        return self._run(script).succeeded

    def start(self, ready_file: PurePath | None = None) -> bool:
        """Demand-start the task, optionally awaiting its exact ready event."""
        if ready_file is None:
            return self._run(_START_SCRIPT).succeeded
        ready_token = b64encode(str(ready_file).encode()).decode("ascii")
        script = (
            _PREFIX
            + "$ready=[Text.Encoding]::UTF8.GetString("
            + "[Convert]::FromBase64String('"
            + ready_token
            + _READY_START_SCRIPT_SUFFIX
        )
        return self._run(script).succeeded

    def stop(self) -> bool:
        """Stop running instances of the task."""
        return self._run(_STOP_SCRIPT).succeeded

    def delete(self) -> bool:
        """Delete the task from the current-user root folder."""
        return self._run(_DELETE_SCRIPT).succeeded

    def _run(self, script: str) -> _RunResult:
        argv = (
            trusted_notifier_path("win32"),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            b64encode(script.encode("utf-16-le")).decode("ascii"),
        )
        return self.runner.run(argv)


def _sanitized_environment() -> Mapping[str, str]:
    blocked = {
        "COMSPEC",
        "DYLD_INSERT_LIBRARIES",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PSModulePath",
        "PYTHONHOME",
        "PYTHONPATH",
        "__COMPAT_LAYER",
    }
    environment = {
        key: value for key, value in os.environ.items() if key not in blocked
    }
    powershell = PureWindowsPath(trusted_notifier_path("win32"))
    environment["PATH"] = str(powershell.parents[2])
    return environment


def _main_pid_script(expected_pid: int | None) -> str:
    if expected_pid is None:
        return _MAIN_PID_SCRIPT
    return (
        _PREFIX
        + _GET_TASK
        + "$roots=@($task.GetInstances(0)|ForEach-Object {[int]$_.EnginePID})\n"
        + "if($roots.Count -eq 0){exit 0}\n"
        + "$processes=@(Get-CimInstance Win32_Process)\n"
        + "$parents=@{}\n"
        + "foreach($process in $processes){$parents[[int]$process.ProcessId]="
        + "[int]$process.ParentProcessId}\n"
        + f"$candidate={expected_pid}\n"
        + "$cursor=$candidate\n"
        + "while($parents.ContainsKey($cursor)){\n"
        + "  if($roots -contains $cursor){[Console]::Out.Write($candidate);exit 0}\n"
        + "  $next=[int]$parents[$cursor]\n"
        + "  if($next -eq $cursor){break}\n"
        + "  $cursor=$next\n"
        + "}\n"
    )
