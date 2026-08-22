from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import timedelta
from typing import TYPE_CHECKING

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.cli.daemon import DaemonOnceResponse
from proactive_mcp.delivery.daemon import DaemonDependencies, WatcherDaemon
from proactive_mcp.delivery.evaluation import EvaluationPass, PreparedSources
from proactive_mcp.scheduler import EventScheduler
from proactive_mcp.situations.engine import EvaluationResult
from proactive_mcp.situations.inputs import EngineInputs
from proactive_mcp.store import SourceFreshness, Store, UnsafeDatabasePathError
from tests.daemon_test_support import (
    FakeEvaluationRunner,
    RecordingHeartbeat,
    RecordingNotifier,
    RecordingScheduler,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from proactive_mcp.clock import Clock

_START = utc_datetime(2026, 7, 11, 9)
_PID = 7


def _run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "proactive_mcp", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )


def _ok_pass() -> EvaluationPass:
    freshness = SourceFreshness("ok", _START, _START, 0, None)
    result = EvaluationResult(0, 0, 0, 0, 0, 0, (), freshness, freshness)
    return EvaluationPass(result, PreparedSources(EngineInputs()), ())


def _ok_daemon() -> WatcherDaemon:
    clock = FakeClock(_START)
    return WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=clock,
            heartbeat=RecordingHeartbeat(),
            evaluation=FakeEvaluationRunner(result=_ok_pass(), clock=clock),
            notifier=RecordingNotifier(),
        )
    )


def test_daemon_help_exposes_once_and_poll_interval_override() -> None:
    # Given: the installed CLI entry point.

    # When: a user asks the daemon command for help.
    result = _run_cli("daemon", "--help")

    # Then: the once-path and cadence override are advertised.
    assert result.returncode == 0
    assert "--once" in result.stdout
    assert "--poll-interval-minutes" in result.stdout


def test_once_exits_zero_on_a_degraded_pass_without_google_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an isolated database and no Google credentials.
    database = tmp_path / "proactive.db"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    # When: the daemon runs exactly one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()
    payload = DaemonOnceResponse.model_validate_json(captured.out)
    with Store(database, clock=FakeClock(_START)) as store:
        status = store.daemon.status(stale_after=timedelta(minutes=5))

    # Then: a degraded local-only pass is success, not an infrastructure failure.
    assert result == 0
    assert captured.err == ""
    assert payload.sources == "not_configured"
    assert payload.gmail == "not_configured"
    assert payload.calendar == "not_configured"
    assert payload.warning_count > 0
    assert status.liveness == "stopped"
    assert status.cycle_count == 1


def test_once_exits_zero_on_an_ok_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a watcher whose one pass reports healthy sources.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    def open_ok(_store: Store, _clock: Clock) -> WatcherDaemon:
        return _ok_daemon()

    monkeypatch.setattr(daemon_cli, "open_watcher_daemon", open_ok)

    # When: the daemon runs exactly one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()
    payload = DaemonOnceResponse.model_validate_json(captured.out)

    # Then: a healthy completed pass is also success.
    assert result == 0
    assert payload.sources == "prepared"
    assert payload.gmail == "ok"
    assert payload.calendar == "ok"
    assert payload.warning_count == 0


def test_once_exits_one_on_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the local store cannot be opened.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    def unavailable(_path: Path, *, clock: Clock | None = None) -> Store:
        del clock
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(daemon_cli, "Store", unavailable)

    # When: the once-path tries to start.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: the failure is infrastructure, with no traceback or exception dump.
    assert result == 1
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err
    assert "No space left on device" not in captured.err


def test_malformed_config_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a config.toml the settings model cannot represent.
    _ = (tmp_path / "config.toml").write_text(
        "[daemon]\npoll_interval_minutes = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    # When: the daemon reads startup configuration.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: a precondition error is reported without a traceback.
    assert result == 2
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err


def test_invalid_poll_interval_override_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a CLI cadence override that is not a positive span.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    # When: the daemon parses the override before starting.
    result = cli.main(["daemon", "--once", "--poll-interval-minutes", "0"])
    captured = capsys.readouterr()

    # Then: the override is rejected as a startup precondition.
    assert result == 2
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err


def test_unsafe_database_path_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the store rejects the configured database location.
    database = tmp_path / "proactive.db"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    def rejected(_path: Path, *, clock: Clock | None = None) -> Store:
        del clock
        raise UnsafeDatabasePathError(path=database, reason="symlink")

    monkeypatch.setattr(daemon_cli, "Store", rejected)

    # When: the daemon tries to open local state.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: the unsafe path is a startup precondition, not infrastructure.
    assert result == 2
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert str(database) not in captured.err


def test_poll_interval_override_wins_over_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: config asks for 7 minutes and the CLI asks for 3.
    _ = (tmp_path / "config.toml").write_text(
        "[daemon]\npoll_interval_minutes = 7\n",
        encoding="utf-8",
    )
    scheduler = RecordingScheduler(stop_after=1)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))
    monkeypatch.setattr(daemon_cli, "stopping_scheduler", lambda: scheduler)

    # When: the continuous loop runs until the scheduler ends it.
    result = cli.main(["daemon", "--poll-interval-minutes", "3"])

    # Then: the CLI override is the cadence that was waited.
    assert result == 0
    assert scheduler.waits == [timedelta(minutes=3)]


def test_continuous_mode_uses_configured_poll_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: config sets a non-default cadence and the CLI does not override it.
    database = tmp_path / "proactive.db"
    _ = (tmp_path / "config.toml").write_text(
        "[daemon]\npoll_interval_minutes = 7\n",
        encoding="utf-8",
    )
    scheduler = RecordingScheduler(stop_after=1)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))
    monkeypatch.setattr(daemon_cli, "stopping_scheduler", lambda: scheduler)

    # When: the continuous loop runs until the scheduler ends it.
    result = cli.main(["daemon"])
    with Store(database, clock=FakeClock(_START)) as store:
        status = store.daemon.status(stale_after=timedelta(minutes=5))

    # Then: the configured cadence is waited and the process records a stop.
    assert result == 0
    assert scheduler.waits == [timedelta(minutes=7)]
    assert status.liveness == "stopped"
    assert status.cycle_count == 1


def test_once_does_not_start_the_continuous_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a once-path invocation.
    started: list[bool] = []
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    def unexpected() -> RecordingScheduler:
        started.append(True)
        return RecordingScheduler(stop_after=1)

    monkeypatch.setattr(daemon_cli, "stopping_scheduler", unexpected)

    # When: the daemon runs exactly one pass.
    result = cli.main(["daemon", "--once"])

    # Then: the continuous wait path is never entered.
    assert result == 0
    assert started == []


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


def test_once_subprocess_exits_without_hanging_and_stays_pii_free(
    tmp_path: Path,
) -> None:
    # Given: an isolated database and no Google credentials.
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "proactive.db")}

    # When: a real process runs the once-path.
    result = _run_cli("daemon", "--once", env=env)

    # Then: it exits successfully with only structural, non-secret output.
    assert result.returncode == 0
    combined = f"{result.stdout}{result.stderr}"
    assert "Traceback" not in combined
    assert "@" not in combined
    payload = DaemonOnceResponse.model_validate_json(result.stdout)
    assert payload.sources == "not_configured"
