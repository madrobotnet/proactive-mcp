from __future__ import annotations

import os
import stat
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Never

from typing_extensions import override

from proactive_mcp.cli import service_darwin
from proactive_mcp.cli.service_launchagent import (
    render_launch_agent,
    write_launch_agent_artifact,
)
from proactive_mcp.cli.service_launchd import LaunchdState
from tests.service_darwin_support import (
    ENTRYPOINT,
    PID,
    FakeDaemonStatus,
    FakeLaunchdManager,
    FakeStatus,
    make_harness,
)

if TYPE_CHECKING:
    import pytest


def _assert_mode(path: Path, expected: int) -> None:
    if os.name != "nt":
        assert stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) == expected


def test_install_reports_strict_ready_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_harness(tmp_path, monkeypatch)

    result = service_darwin.execute_service("install")

    assert result.success is True
    assert result.response.state == "installed"
    assert result.response.unit == "io.github.madrobotnet.proactive-mcp"
    assert result.response.managed is True
    assert result.response.enabled is True
    assert result.response.active is True
    assert result.response.main_pid == PID
    assert result.response.heartbeat == "running"
    assert result.response.linger == "not_applicable"
    assert result.response.guidance == "none"
    _assert_mode(harness.plist, 0o600)


def test_install_status_and_remove_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_harness(tmp_path, monkeypatch)
    assert service_darwin.execute_service("install").success is True
    first = harness.plist.read_bytes()
    calls = list(harness.manager.calls)

    repeated = service_darwin.execute_service("install")
    repeated_calls = list(harness.manager.calls)
    status = service_darwin.execute_service("status")
    removed = service_darwin.execute_service("remove")
    absent = service_darwin.execute_service("remove")

    assert repeated.success is True
    assert repeated_calls == calls
    assert status.success is True
    assert status.response.state == "active"
    assert status.response.main_pid == PID
    assert removed.response.state == "removed"
    assert absent.response.state == "absent"
    assert first.startswith(b"<?xml")
    assert not harness.plist.exists()


def test_unmanaged_plist_is_never_mutated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_harness(tmp_path, monkeypatch)
    harness.plist.parent.mkdir(parents=True)
    original = b"foreign launch agent"
    _ = harness.plist.write_bytes(original)
    harness.plist.chmod(0o644)

    results = [
        service_darwin.execute_service(action)
        for action in ("install", "status", "remove")
    ]

    assert all(result.success is False for result in results)
    assert all(result.response.state == "unmanaged" for result in results)
    assert all(result.response.code == "unmanaged_unit" for result in results)
    assert harness.plist.read_bytes() == original
    _assert_mode(harness.plist, 0o644)
    assert harness.manager.calls == []


def test_loaded_label_without_owned_plist_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeLaunchdManager(
        LaunchdState(loaded=True, enabled=True, active=True, pid=PID)
    )
    harness = make_harness(tmp_path, monkeypatch, manager=manager)

    result = service_darwin.execute_service("install")

    assert result.success is False
    assert result.response.state == "unmanaged"
    assert result.response.code == "unmanaged_unit"
    assert not harness.plist.exists()


def test_install_aborts_before_write_when_launchd_state_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingInspectionManager(FakeLaunchdManager):
        @override
        def state(self) -> LaunchdState:
            raise OSError

    harness = make_harness(
        tmp_path,
        monkeypatch,
        manager=FailingInspectionManager(),
    )

    result = service_darwin.execute_service("install")

    assert result.success is False
    assert result.response.code == "io_failed"
    assert not harness.plist.exists()
    assert harness.manager.calls == []


def test_new_install_failure_removes_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeLaunchdManager(fail_once={"bootstrap"})
    harness = make_harness(tmp_path, monkeypatch, manager=manager)

    result = service_darwin.execute_service("install")

    assert result.success is False
    assert result.response.code == "command_failed"
    assert not harness.plist.exists()
    assert manager.loaded is False
    assert manager.active is False
    assert "bootout" not in manager.calls


def test_write_failure_after_bootout_restores_previous_manager_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeLaunchdManager(
        LaunchdState(loaded=True, enabled=True, active=True, pid=PID)
    )
    harness = make_harness(tmp_path, monkeypatch, manager=manager)
    harness.plist.parent.mkdir(parents=True)
    previous = render_launch_agent(
        ENTRYPOINT,
        PurePosixPath("/previous/database.db"),
    )
    write_launch_agent_artifact(harness.plist, previous)

    def fail_write(_path: Path, _content: bytes, *, mode: int = 0o600) -> Never:
        del mode
        raise OSError

    monkeypatch.setattr(service_darwin, "write_launch_agent_artifact", fail_write)

    result = service_darwin.execute_service("install")

    assert result.success is False
    assert result.response.code == "io_failed"
    assert harness.plist.read_bytes() == previous
    assert manager.enabled is True
    assert manager.loaded is True
    assert manager.active is True


def test_failed_replacement_restores_previous_plist_mode_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeLaunchdManager(
        LaunchdState(loaded=True, enabled=True, active=True, pid=PID),
        fail_once={"bootstrap"},
    )
    harness = make_harness(tmp_path, monkeypatch, manager=manager)
    harness.plist.parent.mkdir(parents=True)
    previous = render_launch_agent(
        ENTRYPOINT,
        PurePosixPath("/previous/database.db"),
    )
    write_launch_agent_artifact(harness.plist, previous, mode=0o640)

    result = service_darwin.execute_service("install")

    assert result.success is False
    assert result.response.code == "command_failed"
    assert harness.plist.read_bytes() == previous
    _assert_mode(harness.plist, 0o640)
    assert manager.enabled is True
    assert manager.loaded is True
    assert manager.active is True


def test_remove_value_failure_restores_previous_manager_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeLaunchdManager()
    harness = make_harness(tmp_path, monkeypatch, manager=manager)
    assert service_darwin.execute_service("install").success is True

    def fail_delete(_path: Path) -> Never:
        raise ValueError

    monkeypatch.setattr(service_darwin, "delete_launch_agent_artifact", fail_delete)

    result = service_darwin.execute_service("remove")

    assert result.success is False
    assert result.response.code == "invalid_value"
    assert harness.plist.exists()
    assert manager.enabled is True
    assert manager.loaded is True
    assert manager.active is True


def test_readiness_stops_when_deadline_is_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_harness(tmp_path, monkeypatch)
    heartbeat_checks = 0

    def stopped_status() -> FakeStatus:
        nonlocal heartbeat_checks
        heartbeat_checks += 1
        return FakeStatus(
            daemon=FakeDaemonStatus(
                liveness="stopped",
                pid=None,
            )
        )

    ticks = iter((0.0, 6.0))

    def no_sleep(_interval: float) -> None:
        return

    monkeypatch.setattr(service_darwin, "_READINESS_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(service_darwin, "build_status", stopped_status)
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", no_sleep)

    result = service_darwin.execute_service("install")

    assert result.success is False
    assert result.response.code == "heartbeat_unavailable"
    assert not harness.plist.exists()
    assert heartbeat_checks == 1


def test_pid_mismatch_rolls_back_and_status_is_unhealthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeLaunchdManager(pid=PID + 1)
    harness = make_harness(tmp_path, monkeypatch, manager=manager)

    install = service_darwin.execute_service("install")

    assert install.success is False
    assert install.response.code == "heartbeat_unavailable"
    assert not harness.plist.exists()


def test_remove_preserves_profile_and_other_launch_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_harness(tmp_path, monkeypatch)
    harness.database.parent.mkdir(parents=True)
    _ = harness.database.write_bytes(b"database")
    config = harness.database.parent / "config.toml"
    oauth = harness.database.parent / "google-oauth.json"
    other = harness.plist.parent / "com.example.other.plist"
    _ = config.write_bytes(b"config")
    _ = oauth.write_bytes(b"oauth")
    assert service_darwin.execute_service("install").success is True
    _ = other.write_bytes(b"other")

    result = service_darwin.execute_service("remove")

    assert result.success is True
    assert not harness.plist.exists()
    assert harness.database.read_bytes() == b"database"
    assert config.read_bytes() == b"config"
    assert oauth.read_bytes() == b"oauth"
    assert other.read_bytes() == b"other"
