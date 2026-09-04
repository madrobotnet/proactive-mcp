from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from proactive_mcp.cli import service_darwin
from proactive_mcp.cli.service_darwin_models import DarwinLayout
from proactive_mcp.cli.service_launchagent import LAUNCHAGENT_FILENAME
from proactive_mcp.cli.service_launchd import LaunchdState

if TYPE_CHECKING:
    import pytest

PID = os.getpid()
ENTRYPOINT = Path(__file__).parents[1] / ".venv" / "bin" / "proactive-mcp"
Operation = Literal["enable", "disable", "bootstrap", "bootout", "kickstart"]


class FakeLaunchdManager:
    """Mutable deterministic launchd state machine used at the manager boundary."""

    enabled: bool
    loaded: bool
    active: bool
    pid: int
    fail_once: set[Operation]
    calls: list[Operation]

    def __init__(
        self,
        state: LaunchdState | None = None,
        *,
        pid: int = PID,
        fail_once: set[Operation] | None = None,
    ) -> None:
        initial = (
            LaunchdState(loaded=False, enabled=True, active=False, pid=None)
            if state is None
            else state
        )
        self.enabled = initial.enabled
        self.loaded = initial.loaded
        self.active = initial.active
        self.pid = pid
        self.fail_once = set() if fail_once is None else fail_once
        self.calls = []

    def state(self) -> LaunchdState:
        return LaunchdState(
            enabled=self.enabled,
            loaded=self.loaded,
            active=self.active,
            pid=self.pid if self.active else None,
        )

    def enable(self) -> bool:
        if self._fails("enable"):
            return False
        self.enabled = True
        return True

    def disable(self) -> bool:
        if self._fails("disable"):
            return False
        self.enabled = False
        return True

    def bootstrap(self, _plist: Path) -> bool:
        if self._fails("bootstrap"):
            return False
        self.loaded = True
        self.active = True
        return True

    def bootout(self) -> bool:
        if self._fails("bootout"):
            return False
        self.loaded = False
        self.active = False
        return True

    def kickstart(self, *, kill: bool = True) -> bool:
        del kill
        if self._fails("kickstart"):
            return False
        self.loaded = True
        self.active = True
        return True

    def _fails(self, operation: Operation) -> bool:
        self.calls.append(operation)
        if operation not in self.fail_once:
            return False
        self.fail_once.remove(operation)
        return True


@dataclass(frozen=True, slots=True)
class FakeDaemonStatus:
    liveness: Literal["running", "stopped", "stale", "never_started"] = "running"
    pid: int | None = PID


@dataclass(frozen=True, slots=True)
class FakeStatus:
    daemon: FakeDaemonStatus


@dataclass(frozen=True, slots=True)
class Harness:
    home: Path
    database: Path
    plist: Path
    manager: FakeLaunchdManager


def make_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manager: FakeLaunchdManager | None = None,
    heartbeat: FakeDaemonStatus | None = None,
) -> Harness:
    home = tmp_path / "home"
    database = tmp_path / "state" / "proactive.db"
    plist = home / "Library" / "LaunchAgents" / LAUNCHAGENT_FILENAME
    launchd = FakeLaunchdManager() if manager is None else manager
    daemon = FakeDaemonStatus() if heartbeat is None else heartbeat
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(service_darwin, "_MANAGER", launchd)
    monkeypatch.setattr(
        service_darwin,
        "_layout",
        lambda: DarwinLayout(plist, database),
    )
    monkeypatch.setattr(service_darwin, "_READINESS_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(service_darwin, "current_executable", lambda: ENTRYPOINT)
    monkeypatch.setattr(
        service_darwin,
        "build_status",
        lambda: FakeStatus(daemon=daemon),
    )
    return Harness(home, database, plist, launchd)
