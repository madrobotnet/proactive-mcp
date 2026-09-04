"""Bounded subprocess adapter for the macOS launchd user manager."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

from typing_extensions import override

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "LaunchctlResult",
    "LaunchctlRunner",
    "LaunchdInspectionError",
    "LaunchdState",
    "LaunchdUserManager",
    "SubprocessLaunchctlRunner",
]

_LAUNCHCTL: Final[str] = "/bin/launchctl"
_TIMEOUT_SECONDS: Final[int] = 2
_SERVICE_NOT_FOUND_EXIT_CODES: Final = frozenset({113})
_PRINT: Final = "print"
_PRINT_DISABLED: Final = "print-disabled"
_COMMAND_FAILED: Final = "command_failed"
_MALFORMED: Final = "malformed"
_SERVICE_NOT_FOUND_RE: Final[re.Pattern[str]] = re.compile(
    r"could not find (?:specified )?service",
    re.IGNORECASE,
)
_DISABLED_SERVICES_DICT_RE: Final[re.Pattern[str]] = re.compile(
    r"disabled\s+services\s*=\s*\{(?P<body>[\s\S]*?)\}",
    re.IGNORECASE,
)
_DISABLED_SERVICE_RE: Final[re.Pattern[str]] = re.compile(
    r'"(?P<label>[^"]+)"\s*=>\s*(?P<disabled>true|false)',
    re.IGNORECASE,
)
_STATE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*state\s*=\s*(?P<state>\w+)",
    re.MULTILINE,
)
_PID_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*pid\s*=\s*(?P<pid>\d+)",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class LaunchctlResult:
    """Execution outcome of one launchctl invocation."""

    succeeded: bool
    output: str
    exit_code: int


@dataclass(frozen=True, slots=True)
class LaunchdInspectionError(OSError):
    """Report launchd output that cannot safely establish current state."""

    operation: Literal["print", "print-disabled"]
    reason: Literal["command_failed", "malformed"]

    @override
    def __str__(self) -> str:
        """Return a stable diagnostic without launchctl output."""
        return f"launchctl {self.operation} {self.reason.replace('_', ' ')}"


@dataclass(frozen=True, slots=True)
class LaunchdState:
    """Inspected state of a launchd service."""

    loaded: bool
    enabled: bool
    active: bool
    pid: int | None


class LaunchctlRunner(Protocol):
    """Execution boundary for launchctl commands."""

    def run(self, *argv: str) -> LaunchctlResult:
        """Run an argv command tuple without shell interpolation."""
        ...


@dataclass(frozen=True, slots=True)
class SubprocessLaunchctlRunner:
    """Default implementation executing launchctl via subprocess.run."""

    timeout: int = _TIMEOUT_SECONDS

    def run(self, *argv: str) -> LaunchctlResult:
        """Execute argv directly using shell=False."""
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return LaunchctlResult(succeeded=False, output="", exit_code=-1)

        combined_output = completed.stdout
        if completed.stderr:
            if combined_output:
                combined_output = f"{combined_output}\n{completed.stderr}"
            else:
                combined_output = completed.stderr

        return LaunchctlResult(
            succeeded=completed.returncode == 0,
            output=combined_output,
            exit_code=completed.returncode,
        )


@dataclass(frozen=True, slots=True)
class LaunchdUserManager:
    """Launchd lifecycle operations for a user domain."""

    label: str
    uid: int | None = None
    runner: LaunchctlRunner = SubprocessLaunchctlRunner()

    @property
    def domain(self) -> str:
        """Return the target launchd user domain (gui/<uid>)."""
        resolved_uid = os.getuid() if self.uid is None else self.uid
        return f"gui/{resolved_uid}"

    @property
    def target(self) -> str:
        """Return the target service specifier (gui/<uid>/<label>)."""
        return f"{self.domain}/{self.label}"

    def enable(self) -> bool:
        """Enable service in user domain."""
        result = self.runner.run(_LAUNCHCTL, "enable", self.target)
        return result.succeeded

    def disable(self) -> bool:
        """Disable service in user domain."""
        result = self.runner.run(_LAUNCHCTL, "disable", self.target)
        return result.succeeded

    def bootstrap(self, plist_path: Path) -> bool:
        """Bootstrap service plist into user domain."""
        if not plist_path.is_absolute():
            return False
        result = self.runner.run(_LAUNCHCTL, "bootstrap", self.domain, str(plist_path))
        return result.succeeded

    def bootout(self) -> bool:
        """Bootout service from user domain, treating already-absent as success."""
        result = self.runner.run(_LAUNCHCTL, "bootout", self.target)
        if result.succeeded:
            return True

        print_result = self.runner.run(_LAUNCHCTL, "print", self.target)
        return _service_is_absent(print_result)

    def kickstart(self, kill: bool = True) -> bool:
        """Kickstart the service, optionally sending SIGKILL first."""
        argv = [_LAUNCHCTL, "kickstart"]
        if kill:
            argv.append("-k")
        argv.append(self.target)
        result = self.runner.run(*argv)
        return result.succeeded

    def is_enabled(self) -> bool:
        """Return True if service is not disabled in print-disabled output."""
        result = self.runner.run(_LAUNCHCTL, _PRINT_DISABLED, self.domain)
        if not result.succeeded:
            raise LaunchdInspectionError(_PRINT_DISABLED, _COMMAND_FAILED)

        dict_match = _DISABLED_SERVICES_DICT_RE.search(result.output)
        if not dict_match:
            raise LaunchdInspectionError(_PRINT_DISABLED, _MALFORMED)

        body = dict_match.group("body")
        matches = tuple(_DISABLED_SERVICE_RE.finditer(body))
        residual = _DISABLED_SERVICE_RE.sub("", body)
        if re.sub(r"[\s;]+", "", residual):
            raise LaunchdInspectionError(_PRINT_DISABLED, _MALFORMED)
        for match in matches:
            if match.group("label") == self.label:
                return match.group("disabled").lower() != "true"

        return True

    def state(self) -> LaunchdState:
        """Inspect service state conservatively."""
        enabled = self.is_enabled()
        print_result = self.runner.run(_LAUNCHCTL, _PRINT, self.target)
        if not print_result.succeeded:
            if _service_is_absent(print_result):
                return LaunchdState(
                    loaded=False,
                    enabled=enabled,
                    active=False,
                    pid=None,
                )
            raise LaunchdInspectionError(_PRINT, _COMMAND_FAILED)

        state_match = _STATE_RE.search(print_result.output)
        state_str = state_match.group("state").lower() if state_match else None

        pid_match = _PID_RE.search(print_result.output)
        pid: int | None = None
        if pid_match:
            try:
                parsed_pid = int(pid_match.group("pid"))
                if parsed_pid > 0:
                    pid = parsed_pid
            except ValueError:
                pid = None

        active = state_str == "running" and pid is not None and pid > 0
        return LaunchdState(
            loaded=True,
            enabled=enabled,
            active=active,
            pid=pid if active else None,
        )


def _service_is_absent(result: LaunchctlResult) -> bool:
    return not result.succeeded and (
        result.exit_code in _SERVICE_NOT_FOUND_EXIT_CODES
        or _SERVICE_NOT_FOUND_RE.search(result.output) is not None
    )
