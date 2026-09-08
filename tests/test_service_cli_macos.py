from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.cli import service, service_darwin
from proactive_mcp.cli.service_darwin_models import DarwinLayout
from proactive_mcp.cli.service_launchagent import LAUNCHAGENT_FILENAME
from proactive_mcp.cli.service_models import (
    ServiceAction,
    ServiceCommandResult,
    ServiceResponse,
)
from tests.service_darwin_support import (
    ENTRYPOINT,
    FakeDaemonStatus,
    FakeLaunchdManager,
    FakeStatus,
)

if TYPE_CHECKING:
    from pathlib import Path


def _success(action: ServiceAction) -> ServiceCommandResult:
    return ServiceCommandResult(
        response=ServiceResponse(
            action=action,
            state="active" if action == "status" else "installed",
            unit="io.github.madrobotnet.proactive-mcp",
            managed=True,
            enabled=True,
            active=True,
            main_pid=123,
            heartbeat="running",
            linger="not_applicable",
            guidance="none",
            code=None,
        ),
        success=True,
    )


@pytest.mark.parametrize("action", ["install", "status", "remove"])
def test_darwin_routes_every_action_to_typed_backend(
    monkeypatch: pytest.MonkeyPatch,
    action: ServiceAction,
) -> None:
    calls: list[ServiceAction] = []

    def execute(selected: ServiceAction) -> ServiceCommandResult:
        calls.append(selected)
        return _success(selected)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(service_darwin, "execute_service", execute)

    result = service.execute_service(action)

    assert calls == [action]
    assert result == _success(action)
    assert result.response.linger == "not_applicable"


def test_unknown_platform_keeps_closed_typed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "aix")

    result = service.execute_service("status")

    assert result.success is False
    assert result.response.state == "unsupported"
    assert result.response.code == "unsupported_platform"
    assert result.response.linger == "not_applicable"


def test_run_service_remains_the_only_json_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _success("status")

    def executor(_action: ServiceAction) -> ServiceCommandResult:
        return result

    monkeypatch.setattr(service, "execute_service", executor)

    exit_code = service.run_service("status")
    captured = capsys.readouterr()

    assert exit_code == 0
    assert ServiceResponse.model_validate_json(captured.out) == result.response
    assert captured.err == ""


@pytest.mark.parametrize("ready_at", [6.0, 29.0, float("inf")])
def test_install_waits_for_matching_heartbeat_within_thirty_second_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ready_at: float,
) -> None:
    # Given: launchd starts immediately, but its matching heartbeat is delayed.
    manager = FakeLaunchdManager()
    layout = DarwinLayout(tmp_path / LAUNCHAGENT_FILENAME, tmp_path / "proactive.db")
    monkeypatch.setattr(service_darwin, "_MANAGER", manager)
    monkeypatch.setattr(service_darwin, "_layout", lambda: layout)
    monkeypatch.setattr(service_darwin, "current_executable", lambda: ENTRYPOINT)
    monkeypatch.setattr(sys, "platform", "darwin")
    elapsed = 0.0
    intervals: list[float] = []

    def advance(interval: float) -> None:
        nonlocal elapsed
        intervals.append(interval)
        elapsed = round(elapsed + interval, 8)
        assert elapsed <= 30.1, "readiness polling exceeded its bounded budget"

    def status() -> FakeStatus:
        pid = manager.pid
        return FakeStatus(
            daemon=FakeDaemonStatus(pid=pid if elapsed >= ready_at else pid + 1)
        )

    monkeypatch.setattr(
        service_darwin,
        "time",
        SimpleNamespace(monotonic=lambda: elapsed, sleep=advance),
    )
    monkeypatch.setattr(service_darwin, "build_status", status)

    # When: the real install flow runs through the JSON CLI boundary.
    exit_code = service.run_service("install")
    captured = capsys.readouterr()
    response = ServiceResponse.model_validate_json(captured.out)

    # Then: late readiness succeeds, or the full budget expires and rolls back.
    if ready_at < 30.0:
        assert exit_code == 0
        assert response.state == "installed"
        assert response.code is None
        assert response.main_pid == manager.pid
        assert response.heartbeat == "running"
        assert manager.loaded is True
        assert layout.plist.exists()
        assert elapsed == pytest.approx(ready_at)
    else:
        assert exit_code == 2
        assert response.state == "failed"
        assert response.code == "heartbeat_unavailable"
        assert manager.loaded is False
        assert not layout.plist.exists()
        assert elapsed == pytest.approx(30.0)
    assert intervals
    assert set(intervals) == {0.1}
    assert captured.err == ""
