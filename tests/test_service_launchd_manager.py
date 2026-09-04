from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Never, TypedDict, Unpack

from proactive_mcp.cli.service_launchd import (
    LaunchctlResult,
    LaunchdUserManager,
    SubprocessLaunchctlRunner,
)
from tests.service_launchd_manager_support import FakeLaunchctlRunner

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True, slots=True)
class _RunCall:
    argv: tuple[str, ...]
    capture_output: bool
    text: bool
    check: bool
    shell: bool
    timeout: int


class _RunOptions(TypedDict):
    capture_output: bool
    text: bool
    check: bool
    shell: bool
    timeout: int


def test_subprocess_runner_invokes_literal_argv_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_RunCall] = []

    def fake_run(
        args: tuple[str, ...],
        **options: Unpack[_RunOptions],
    ) -> subprocess.CompletedProcess[str]:
        calls.append(_RunCall(argv=args, **options))
        return subprocess.CompletedProcess(args, 0, "output\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessLaunchctlRunner().run(
        "launchctl",
        "print",
        "gui/501/com.proactive.mcp",
    )

    call = calls[0]
    assert call.argv == ("launchctl", "print", "gui/501/com.proactive.mcp")
    assert call.shell is False
    assert call.capture_output is True
    assert call.text is True
    assert call.check is False
    assert call.timeout == 2
    assert result == LaunchctlResult(succeeded=True, output="output\n", exit_code=0)


def test_subprocess_runner_handles_oserror_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_oserror(
        _args: tuple[str, ...],
        **options: Unpack[_RunOptions],
    ) -> Never:
        del options
        raise OSError

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    missing = SubprocessLaunchctlRunner().run("launchctl", "version")

    def raise_timeout(
        _args: tuple[str, ...],
        **options: Unpack[_RunOptions],
    ) -> Never:
        raise subprocess.TimeoutExpired(
            cmd=["launchctl"],
            timeout=options["timeout"],
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    timed_out = SubprocessLaunchctlRunner().run("launchctl", "version")

    failed = LaunchctlResult(succeeded=False, output="", exit_code=-1)
    assert missing == failed
    assert timed_out == failed


def test_manager_enable_argv() -> None:
    runner = FakeLaunchctlRunner()
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.enable() is True
    assert runner.recorded_calls == [
        ("/bin/launchctl", "enable", "gui/501/com.proactive.mcp")
    ]


def test_manager_disable_argv() -> None:
    runner = FakeLaunchctlRunner()
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.disable() is True
    assert runner.recorded_calls == [
        ("/bin/launchctl", "disable", "gui/501/com.proactive.mcp")
    ]


def test_manager_bootstrap_argv(tmp_path: Path) -> None:
    runner = FakeLaunchctlRunner()
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)
    plist = tmp_path / "com.proactive.mcp.plist"

    assert manager.bootstrap(plist) is True
    assert runner.recorded_calls == [
        ("/bin/launchctl", "bootstrap", "gui/501", str(plist))
    ]


def test_manager_rejects_relative_bootstrap_path() -> None:
    runner = FakeLaunchctlRunner()
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.bootstrap(Path("relative.plist")) is False
    assert runner.recorded_calls == []


def test_manager_bootout_argv() -> None:
    runner = FakeLaunchctlRunner()
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.bootout() is True
    assert runner.recorded_calls == [
        ("/bin/launchctl", "bootout", "gui/501/com.proactive.mcp")
    ]


def test_manager_bootout_is_idempotent_when_absent() -> None:
    bootout = ("/bin/launchctl", "bootout", "gui/501/com.proactive.mcp")
    inspect = ("/bin/launchctl", "print", "gui/501/com.proactive.mcp")
    runner = FakeLaunchctlRunner(
        {
            bootout: LaunchctlResult(
                succeeded=False,
                output="Could not find service",
                exit_code=3,
            ),
            inspect: LaunchctlResult(
                succeeded=False,
                output="Could not find service",
                exit_code=113,
            ),
        }
    )
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.bootout() is True
    assert runner.recorded_calls == [bootout, inspect]


def test_manager_bootout_preserves_real_failure() -> None:
    bootout = ("/bin/launchctl", "bootout", "gui/501/com.proactive.mcp")
    inspect = ("/bin/launchctl", "print", "gui/501/com.proactive.mcp")
    runner = FakeLaunchctlRunner(
        {
            bootout: LaunchctlResult(
                succeeded=False,
                output="Operation not permitted",
                exit_code=1,
            ),
            inspect: LaunchctlResult(
                succeeded=True,
                output="state = running",
                exit_code=0,
            ),
        }
    )
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.bootout() is False


def test_manager_bootout_does_not_treat_unknown_inspection_failure_as_absent() -> None:
    bootout = ("/bin/launchctl", "bootout", "gui/501/com.proactive.mcp")
    inspect = ("/bin/launchctl", "print", "gui/501/com.proactive.mcp")
    runner = FakeLaunchctlRunner(
        {
            bootout: LaunchctlResult(
                succeeded=False,
                output="Operation not permitted",
                exit_code=1,
            ),
            inspect: LaunchctlResult(succeeded=False, output="", exit_code=-1),
        }
    )
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.bootout() is False


def test_manager_kickstart_argv() -> None:
    runner = FakeLaunchctlRunner()
    manager = LaunchdUserManager("com.proactive.mcp", uid=501, runner=runner)

    assert manager.kickstart(kill=True) is True
    assert runner.recorded_calls == [
        ("/bin/launchctl", "kickstart", "-k", "gui/501/com.proactive.mcp")
    ]
