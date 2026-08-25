"""Bounded subprocess adapter for the systemd user manager."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Final

from proactive_mcp.cli.service_models import LingerState

__all__ = ["LingerState", "SystemdUserManager"]

_SYSTEMCTL: Final = ("systemctl", "--user")


@dataclass(frozen=True, slots=True)
class _CommandResult:
    succeeded: bool
    output: str


@dataclass(frozen=True, slots=True)
class SystemdUserManager:
    """Expose only the systemd operations used by the managed unit lifecycle."""

    unit_name: str

    def reload(self) -> bool:
        """Reload user units."""
        return self._run(*_SYSTEMCTL, "daemon-reload").succeeded

    def enable(self) -> bool:
        """Enable the managed unit for future user-manager starts."""
        return self._run(*_SYSTEMCTL, "enable", self.unit_name).succeeded

    def start(self) -> bool:
        """Start the managed unit now."""
        return self._run(*_SYSTEMCTL, "start", self.unit_name).succeeded

    def stop(self) -> bool:
        """Stop the managed unit now."""
        return self._run(*_SYSTEMCTL, "stop", self.unit_name).succeeded

    def disable(self) -> bool:
        """Disable the managed unit from future user-manager starts."""
        return self._run(*_SYSTEMCTL, "disable", self.unit_name).succeeded

    def is_enabled(self) -> bool:
        """Return whether the managed unit is enabled."""
        return self._run(*_SYSTEMCTL, "is-enabled", self.unit_name).succeeded

    def is_active(self) -> bool:
        """Return whether the managed unit is active."""
        return self._run(*_SYSTEMCTL, "is-active", self.unit_name).succeeded

    def main_pid(self) -> int | None:
        """Return systemd's current main process identity."""
        result = self._run(
            *_SYSTEMCTL,
            "show",
            self.unit_name,
            "--property=MainPID",
            "--value",
        )
        value = result.output.strip()
        return int(value) if result.succeeded and value.isdecimal() else None

    def linger(self) -> LingerState:
        """Return whether this user manager survives logout."""
        result = self._run(
            "loginctl",
            "show-user",
            str(os.getuid()),
            "--property=Linger",
            "--value",
        )
        if not result.succeeded:
            return "unknown"
        match result.output.strip():
            case "yes":
                return "enabled"
            case "no":
                return "disabled"
            case _:
                return "unknown"

    @staticmethod
    def _run(*arguments: str) -> _CommandResult:
        try:
            # Arguments are closed lifecycle commands, never user input.
            completed = subprocess.run(  # noqa: S603
                arguments,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _CommandResult(succeeded=False, output="")
        return _CommandResult(
            succeeded=completed.returncode == 0,
            output=completed.stdout,
        )
