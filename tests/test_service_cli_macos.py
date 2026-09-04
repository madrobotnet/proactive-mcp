from __future__ import annotations

import sys

import pytest

from proactive_mcp.cli import service, service_darwin
from proactive_mcp.cli.service_models import (
    ServiceAction,
    ServiceCommandResult,
    ServiceResponse,
)


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
