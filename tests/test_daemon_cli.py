"""Daemon CLI integration matrix. # noqa: SIZE_OK - one real CLI surface."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, NoReturn

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.cli.daemon import DaemonOnceResponse
from proactive_mcp.config import load_config
from proactive_mcp.delivery.daemon import DaemonDependencies, WatcherDaemon
from proactive_mcp.delivery.evaluation import (
    EvaluationPass,
    EvaluationService,
    PreparedSources,
    SkippedSources,
)
from proactive_mcp.delivery.fallback import FallbackDispatcher
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.scheduler import EventScheduler
from proactive_mcp.server.situation_tools import open_situation_service
from proactive_mcp.server.status import status_response
from proactive_mcp.situations.engine import EvaluationResult
from proactive_mcp.situations.inputs import EngineInputs
from proactive_mcp.sources.lazy_sync import ScheduledSourceProvider, SourceAccess
from proactive_mcp.store import (
    DaemonStatusStore,
    SourceFreshness,
    Store,
    UnsafeDatabasePathError,
)
from tests.daemon_test_support import (
    FakeCredential,
    FakeCredentialStore,
    FakeEvaluationRunner,
    FakeReaderFactory,
    RecordingHeartbeat,
    RecordingNotifier,
    RecordingScheduler,
    StoreBackedReader,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from proactive_mcp.clock import Clock

_START = utc_datetime(2026, 7, 11, 9)
_PID = 7
_CONFIG_MINUTES = 5
_OVERRIDE_MINUTES = 60
_QUERY_OFFSET = timedelta(minutes=16)
_UNTRUSTED_ERROR_TEXT: Final = (
    "phase-canary /private/path SELECT secret bearer token"
)


class _UntrustedPhaseError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(_UNTRUSTED_ERROR_TEXT)


def _keep_daemon_row_running(_heartbeat: DaemonStatusStore) -> None:
    """Leave the liveness row running after the CLI wait ends."""


def start_live_overridden_watcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProactivePaths, FakeClock]:
    """Start a 60-minute daemon against a 5-minute config and keep it live."""
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        f"[daemon]\npoll_interval_minutes = {_CONFIG_MINUTES}\n",
        encoding="utf-8",
    )
    clock = FakeClock(_START)
    scheduler = RecordingScheduler(stop_after=1)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(paths.database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: clock)
    monkeypatch.setattr(daemon_cli, "stopping_scheduler", lambda: scheduler)
    monkeypatch.setattr(DaemonStatusStore, "record_stop", _keep_daemon_row_running)
    result = cli.main(["daemon", "--poll-interval-minutes", str(_OVERRIDE_MINUTES)])
    assert result == 0
    assert scheduler.waits == [timedelta(minutes=_OVERRIDE_MINUTES)]
    with Store(paths.database, clock=clock) as store:
        assert store.daemon.status().poll_interval == timedelta(
            minutes=_OVERRIDE_MINUTES
        )
        assert store.daemon.status().liveness == "running"
    return paths, clock


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
        status = store.daemon.status()

    # Then: a degraded local-only pass is success, not an infrastructure failure.
    assert result == 0
    assert captured.err == ""
    assert payload.sources == "not_configured"
    assert payload.gmail == "not_configured"
    assert payload.calendar == "not_configured"
    assert payload.warning_count > 0
    assert status.liveness == "stopped"
    assert status.cycle_count == 1
    assert status.poll_interval == timedelta(minutes=_CONFIG_MINUTES)


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

    # Then: the database phase is actionable without exception data.
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "database", "code": "open_failed"}
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
    assert json.loads(captured.err) == {"phase": "config", "code": "invalid"}
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
    assert json.loads(captured.err) == {
        "phase": "database", "code": "unsafe_path"
    }
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
    with Store(tmp_path / "proactive.db", clock=FakeClock(_START)) as store:
        status = store.daemon.status()

    # Then: the CLI override is the cadence that was waited and persisted.
    assert result == 0
    assert scheduler.waits == [timedelta(minutes=3)]
    assert status.poll_interval == timedelta(minutes=3)
    assert status.liveness == "stopped"


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
        status = store.daemon.status()

    # Then: the configured cadence is waited, persisted, and stopped.
    assert result == 0
    assert scheduler.waits == [timedelta(minutes=7)]
    assert status.liveness == "stopped"
    assert status.cycle_count == 1
    assert status.poll_interval == timedelta(minutes=7)


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


def test_notify_service_ready_sends_systemd_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a systemd notification socket subscribed before daemon startup.
    notify_socket = tmp_path / "notify.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(notify_socket))
        receiver.settimeout(1)
        monkeypatch.setenv("NOTIFY_SOCKET", str(notify_socket))

        # When: the daemon announces that startup ownership is persisted.
        daemon_cli.notify_service_ready()

        # Then: systemd receives its readiness event synchronously.
        assert receiver.recv(64) == b"READY=1"


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
        liveness = store.daemon.status().liveness
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "service", "code": "notify_failed"}
    assert "canary" not in captured.err
    assert liveness == "stopped"


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


def test_poll_interval_override_is_visible_to_status_at_sixteen_minutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 5-minute config and a still-running daemon started at 60 minutes.
    paths, clock = start_live_overridden_watcher(tmp_path, monkeypatch)

    # When: status is built 16 minutes after the last heartbeat.
    clock.advance(_QUERY_OFFSET)
    with Store(paths.database, clock=clock) as store:
        status = status_response(store, clock, paths)

    # Then: the CLI override, not the 5-minute file, decides liveness.
    assert load_config(paths.config).daemon.poll_interval == timedelta(
        minutes=_CONFIG_MINUTES
    )
    assert status.daemon.liveness == "running"
    assert status.daemon.status == "running"
    assert status.daemon.cycle_count == 1
    assert all("heartbeat is stale" not in warning for warning in status.warnings)


def test_poll_interval_override_keeps_lazy_sync_from_reading_at_sixteen_minutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: configured sources and a still-running 60-minute override daemon.
    paths, clock = start_live_overridden_watcher(tmp_path, monkeypatch)
    clock.advance(_QUERY_OFFSET)
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        reader = StoreBackedReader(store=store)

        def open_access(
            _paths: ProactivePaths, bound: Store, _clock: Clock
        ) -> SourceAccess:
            return SourceAccess(
                sync_state=bound,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )

        monkeypatch.setattr(
            "proactive_mcp.server.situation_tools.open_source_access",
            open_access,
        )
        service = open_situation_service(store, clock, paths)

        # When: the situation tool path evaluates lazy-sync at +16 minutes.
        _ = service.proactive_check()

    # Then: the live watcher still owns the sources.
    assert reader.reads == []


def test_once_with_override_stops_instead_of_holding_the_cadence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a one-shot daemon started with a 60-minute override.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        f"[daemon]\npoll_interval_minutes = {_CONFIG_MINUTES}\n",
        encoding="utf-8",
    )
    clock = FakeClock(_START)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(paths.database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: clock)

    # When: the once-path completes and 16 minutes elapse.
    result = cli.main(
        ["daemon", "--once", "--poll-interval-minutes", str(_OVERRIDE_MINUTES)]
    )
    _ = capsys.readouterr()
    clock.advance(_QUERY_OFFSET)
    with Store(paths.database, clock=clock) as store:
        observed = store.daemon.status()
        status = status_response(store, clock, paths)
        store.set_google_auth_state("configured")
        reader = StoreBackedReader(store=store)

        def open_access(
            _paths: ProactivePaths, bound: Store, _clock: Clock
        ) -> SourceAccess:
            return SourceAccess(
                sync_state=bound,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )

        monkeypatch.setattr(
            "proactive_mcp.server.situation_tools.open_source_access",
            open_access,
        )
        _ = open_situation_service(store, clock, paths).proactive_check()

    # Then: --once is stopped, so degraded mode may still read sources.
    assert result == 0
    assert observed.liveness == "stopped"
    assert observed.poll_interval == timedelta(minutes=_OVERRIDE_MINUTES)
    assert status.daemon.liveness == "stopped"
    assert status.daemon.status == "not_running"
    assert reader.reads == [1]


def test_source_sync_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: source preparation raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_source(_provider: ScheduledSourceProvider) -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(ScheduledSourceProvider, "prepare_sources", fail_source)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: source failure identity is bounded and safe.
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "source_sync", "code": "failed"}
    assert "canary" not in captured.err


def test_evaluation_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: evaluation raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_evaluation(_service: EvaluationService) -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(EvaluationService, "run_once", fail_evaluation)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: evaluation failure identity is bounded and safe.
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "evaluation", "code": "failed"}
    assert "canary" not in captured.err


def test_notification_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: notification dispatch raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_notification(
        _dispatcher: FallbackDispatcher, _now: datetime
    ) -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(FallbackDispatcher, "dispatch", fail_notification)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: notification failure identity is bounded and safe.
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "notification", "code": "failed"}
    assert "canary" not in captured.err


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
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "heartbeat", "code": "failed"}
    assert "canary" not in captured.err


def test_credential_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: secure credential storage is unavailable for this pass.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def unavailable(_provider: ScheduledSourceProvider) -> SkippedSources:
        return SkippedSources("credential_storage_unavailable")

    monkeypatch.setattr(ScheduledSourceProvider, "prepare_sources", unavailable)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: credential failure identity is bounded and safe.
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "credential", "code": "unavailable"}


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
