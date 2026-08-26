"""Daemon cadence configuration and status integration."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.config import load_config
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_tools import open_situation_service
from proactive_mcp.server.status import status_response
from proactive_mcp.sources.lazy_sync import SourceAccess
from proactive_mcp.store import Store
from tests.daemon_cli_test_support import (
    CONFIG_MINUTES as _CONFIG_MINUTES,
)
from tests.daemon_cli_test_support import (
    OVERRIDE_MINUTES as _OVERRIDE_MINUTES,
)
from tests.daemon_cli_test_support import (
    QUERY_OFFSET as _QUERY_OFFSET,
)
from tests.daemon_cli_test_support import (
    START as _START,
)
from tests.daemon_cli_test_support import (
    start_live_overridden_watcher,
)
from tests.daemon_test_support import (
    FakeCredential,
    FakeCredentialStore,
    FakeReaderFactory,
    RecordingScheduler,
    StoreBackedReader,
)
from tests.situation_test_support import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from proactive_mcp.clock import Clock


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
