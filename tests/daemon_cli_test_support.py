"""Shared process and watcher support for daemon CLI tests."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Self, final

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.cli.daemon import DaemonOnceResponse
from proactive_mcp.delivery.daemon import DaemonDependencies, DaemonPass, WatcherDaemon
from proactive_mcp.delivery.evaluation import EvaluationPass, PreparedSources
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.situations.engine import EvaluationResult
from proactive_mcp.situations.inputs import EngineInputs
from proactive_mcp.store import DaemonStatusStore, SourceFreshness, Store
from tests.daemon_test_support import (
    FakeEvaluationRunner,
    RecordingHeartbeat,
    RecordingNotifier,
    RecordingScheduler,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    import pytest

START: Final = utc_datetime(2026, 7, 11, 9)
PID: Final = 7
CONFIG_MINUTES: Final = 5
OVERRIDE_MINUTES: Final = 60
QUERY_OFFSET: Final = timedelta(minutes=16)
UNTRUSTED_ERROR_TEXT: Final = "phase-canary /private/path SELECT secret bearer token"


class UntrustedPhaseError(RuntimeError):
    """Carry deliberately sensitive-looking text across a failure boundary."""

    def __init__(self) -> None:
        super().__init__(UNTRUSTED_ERROR_TEXT)


@final
class RecordingNotifySocket:
    """Capture one systemd datagram socket connection and payload."""

    __slots__ = ("connected", "payload")

    connected: str | bytes | None
    payload: bytes | None

    def __init__(self) -> None:
        self.connected = None
        self.payload = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def connect(self, address: str | bytes) -> None:
        self.connected = address

    def sendall(self, payload: bytes) -> None:
        self.payload = payload


def _keep_daemon_row_running(_heartbeat: DaemonStatusStore) -> None:
    """Leave the liveness row running after the CLI wait ends."""


def start_live_overridden_watcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProactivePaths, FakeClock]:
    """Start a 60-minute daemon against a 5-minute config and keep it live."""
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        f"[daemon]\npoll_interval_minutes = {CONFIG_MINUTES}\n",
        encoding="utf-8",
    )
    clock = FakeClock(START)
    scheduler = RecordingScheduler(stop_after=1)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(paths.database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: clock)
    monkeypatch.setattr(daemon_cli, "stopping_scheduler", lambda: scheduler)
    monkeypatch.setattr(DaemonStatusStore, "record_stop", _keep_daemon_row_running)
    result = cli.main(["daemon", "--poll-interval-minutes", str(OVERRIDE_MINUTES)])
    assert result == 0
    assert scheduler.waits == [timedelta(minutes=OVERRIDE_MINUTES)]
    with Store(paths.database, clock=clock) as store:
        assert store.daemon.status().poll_interval == timedelta(
            minutes=OVERRIDE_MINUTES
        )
        assert store.daemon.status().liveness == "running"
    return paths, clock


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI module with bounded process completion."""
    return subprocess.run(
        [sys.executable, "-m", "proactive_mcp", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )


def _ok_pass() -> EvaluationPass:
    freshness = SourceFreshness("ok", START, START, 0, None)
    result = EvaluationResult(0, 0, 0, 0, 0, 0, (), freshness, freshness)
    return EvaluationPass(result, PreparedSources(EngineInputs()), ())


def once_payload(
    completed: DaemonPass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> DaemonOnceResponse:
    """Serialize a supplied pass through the real one-shot CLI boundary."""
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(START))

    def complete_once(_daemon: WatcherDaemon) -> DaemonPass:
        return completed

    monkeypatch.setattr(WatcherDaemon, "run_once", complete_once)
    assert cli.main(["daemon", "--once"]) == 0
    return DaemonOnceResponse.model_validate_json(capsys.readouterr().out)


def ok_daemon() -> WatcherDaemon:
    """Build a watcher whose single pass reports healthy sources."""
    clock = FakeClock(START)
    return WatcherDaemon(
        DaemonDependencies(
            pid=PID,
            clock=clock,
            heartbeat=RecordingHeartbeat(),
            evaluation=FakeEvaluationRunner(result=_ok_pass(), clock=clock),
            notifier=RecordingNotifier(),
        )
    )
