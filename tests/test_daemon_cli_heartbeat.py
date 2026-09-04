"""Daemon heartbeat ownership, signals, and systemd notification."""

from __future__ import annotations

import json
import os
import signal
import socket
from datetime import timedelta
from typing import TYPE_CHECKING, NoReturn

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.scheduler import EventScheduler
from proactive_mcp.store import DaemonStatusStore, Store
from tests.daemon_cli_test_support import (
    CONFIG_MINUTES as _CONFIG_MINUTES,
)
from tests.daemon_cli_test_support import (
    START as _START,
)
from tests.daemon_cli_test_support import (
    RecordingNotifySocket as _RecordingNotifySocket,
)
from tests.daemon_cli_test_support import (
    UntrustedPhaseError as _UntrustedPhaseError,
)
from tests.daemon_test_support import RecordingScheduler
from tests.situation_test_support import FakeClock

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


def test_sigint_stops_the_continuous_scheduler_without_waiting() -> None:
    # Given: a live scheduler bound to the CLI stop handlers.
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    scheduler = EventScheduler()
    try:
        daemon_cli.bind_stop_signals(scheduler)

        # When: the process receives Ctrl+C.
        signal.raise_signal(signal.SIGINT)

        # Then: the next wait ends immediately and stops the loop.
        assert scheduler.wait(timedelta(hours=1)) is False
    finally:
        _ = signal.signal(signal.SIGINT, previous_int)
        _ = signal.signal(signal.SIGTERM, previous_term)


def test_notify_service_ready_sends_systemd_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a systemd notification socket subscribed before daemon startup.
    notifier = _RecordingNotifySocket()

    def open_notifier(
        family: socket.AddressFamily,
        kind: socket.SocketKind,
    ) -> _RecordingNotifySocket:
        assert family == unix_family
        assert kind == socket.SOCK_DGRAM
        return notifier

    unix_family = getattr(socket, "AF_UNIX", 1)
    monkeypatch.setattr(socket, "AF_UNIX", unix_family, raising=False)
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/user/1000/notify")
    monkeypatch.setattr(socket, "socket", open_notifier)

    # When: the daemon announces that startup ownership is persisted.
    daemon_cli.notify_service_ready()

    # Then: systemd receives its readiness event synchronously.
    assert notifier.connected == "/run/user/1000/notify"
    assert notifier.payload == b"READY=1"


def test_service_notify_failure_is_redacted_and_releases_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: systemd readiness notification fails with untrusted details.
    database = tmp_path / "proactive.db"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))

    def fail_notify() -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(daemon_cli, "notify_service_ready", fail_notify)

    # When: the continuous daemon reaches its readiness boundary.
    result = cli.main(["daemon"])
    captured = capsys.readouterr()

    # Then: the failure is bounded and the aborted owner is stopped.
    with Store(database) as store:
        status = store.daemon.status()
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "service", "code": "notify_failed"}
    assert "canary" not in captured.err
    assert status.liveness == "stopped"
    assert status.mode == "continuous"
    assert status.last_run_state == "failed"
    assert status.last_failure_phase == "service"
    assert status.last_failure_code == "notify_failed"


def test_heartbeat_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the heartbeat store raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_heartbeat(
        _heartbeat: DaemonStatusStore,
        _pid: int,
        *,
        poll_interval: timedelta | None = None,
    ) -> NoReturn:
        del poll_interval
        raise _UntrustedPhaseError

    monkeypatch.setattr(DaemonStatusStore, "try_record_start", fail_heartbeat)

    # When: the daemon starts one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: heartbeat failure identity is bounded and safe.
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "heartbeat", "code": "failed"}
    assert "canary" not in captured.err


def test_notify_service_ready_signals_scheduler_after_heartbeat_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "proactive.db"
    signaled: list[Path] = []
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)

    def signal_ready(path: Path) -> None:
        signaled.append(path)

    monkeypatch.setattr(
        daemon_cli,
        "signal_task_scheduler_ready",
        signal_ready,
    )

    daemon_cli.notify_service_ready()
    assert signaled == [database]


def test_runtime_ownership_conflict_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: another live daemon owns the singleton runtime row.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def reject(
        _heartbeat: DaemonStatusStore,
        _pid: int,
        *,
        poll_interval: timedelta | None = None,
        incumbent_is_alive: Callable[[int], bool] | None = None,
    ) -> bool:
        del poll_interval, incumbent_is_alive
        return False

    monkeypatch.setattr(DaemonStatusStore, "try_record_start", reject)

    # When: the challenger starts one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: ownership rejection is failure, never misleading success.
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "phase": "runtime_ownership",
        "code": "ownership_conflict",
    }


def test_continuous_daemon_reclaims_dead_owner_after_systemd_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a crashed daemon left a fresh running row behind.
    database = tmp_path / "proactive.db"
    clock = FakeClock(_START)
    with Store(database, clock=clock) as crashed:
        assert crashed.daemon.try_record_start(
            pid=4242,
            poll_interval=timedelta(minutes=_CONFIG_MINUTES),
        )
    scheduler = RecordingScheduler(stop_after=1)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: clock)
    monkeypatch.setattr(daemon_cli, "stopping_scheduler", lambda: scheduler)

    def process_is_dead(_pid: int) -> bool:
        return False

    monkeypatch.setattr(
        daemon_cli,
        "_process_is_alive",
        process_is_dead,
        raising=False,
    )

    # When: systemd starts a replacement before the old heartbeat expires.
    result = cli.main(["daemon"])
    with Store(database, clock=clock) as store:
        status = store.daemon.status()

    # Then: the dead owner is replaced and the new daemon runs one clean cycle.
    assert result == 0
    assert status.pid == os.getpid()
    assert status.liveness == "stopped"
    assert status.cycle_count == 1
