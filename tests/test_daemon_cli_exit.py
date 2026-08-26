"""Daemon CLI exit taxonomy and startup preconditions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, NoReturn

import pytest

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.delivery.daemon import DaemonFailureError, DaemonFailureKind
from proactive_mcp.delivery.evaluation import SkippedSources
from proactive_mcp.sources.lazy_sync import ScheduledSourceProvider
from proactive_mcp.store import Store, UnsafeDatabasePathError
from tests.daemon_cli_test_support import (
    START as _START,
)
from tests.daemon_cli_test_support import (
    run_cli as _run_cli,
)
from tests.situation_test_support import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.clock import Clock


@pytest.mark.parametrize(
    ("kind", "expected_exit_status"),
    [
        (DaemonFailureKind.CONFIG_INVALID, 2),
        (DaemonFailureKind.DATABASE_UNSAFE_PATH, 2),
        (DaemonFailureKind.DATABASE_OPEN_FAILED, 1),
        (DaemonFailureKind.CREDENTIAL_UNAVAILABLE, 2),
        (DaemonFailureKind.SOURCE_SYNC_FAILED, 1),
        (DaemonFailureKind.EVALUATION_FAILED, 1),
        (DaemonFailureKind.NOTIFICATION_FAILED, 1),
        (DaemonFailureKind.HEARTBEAT_FAILED, 1),
        (DaemonFailureKind.OWNERSHIP_CONFLICT, 2),
        (DaemonFailureKind.SERVICE_NOTIFY_FAILED, 1),
    ],
)
def test_daemon_failure_kind_controls_systemd_restart_exit_status(
    kind: DaemonFailureKind,
    expected_exit_status: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: startup reaches one closed daemon failure classification.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    failure = DaemonFailureError(kind)

    def fail_startup(_path: Path) -> NoReturn:
        raise failure

    monkeypatch.setattr(daemon_cli, "load_config", fail_startup)

    # When: the daemon process boundary handles the failure.
    result = daemon_cli.run_daemon(once=True, poll_interval_minutes=None)
    captured = capsys.readouterr()

    # Then: permanent failures stop restart while retryable failures request it.
    assert result == expected_exit_status
    assert failure.kind is kind
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "phase": failure.phase,
        "code": failure.code,
    }


def test_daemon_help_exposes_once_and_poll_interval_override() -> None:
    # Given: the installed CLI entry point.

    # When: a user asks the daemon command for help.
    result = _run_cli("daemon", "--help")

    # Then: the once-path and cadence override are advertised.
    assert result.returncode == 0
    assert "--once" in result.stdout
    assert "--poll-interval-minutes" in result.stdout


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
    assert result == 1
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
    assert json.loads(captured.err) == {"phase": "database", "code": "unsafe_path"}
    assert "Traceback" not in captured.err
    assert str(database) not in captured.err


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
