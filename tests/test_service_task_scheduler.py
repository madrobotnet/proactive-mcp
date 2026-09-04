from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath
from typing import Literal

import pytest

from proactive_mcp.cli import service as service_module
from proactive_mcp.cli.service_models import (
    ServiceAction,
    ServiceCommandResult,
    ServiceResponse,
)
from tests.windows_scheduler_security_support import (
    assert_native_system_directory,
    assert_register_embeds_definition_in_encoded_command,
)
from tests.windows_service_support import (
    PID,
    POWERSHELL,
    assert_encoded_launcher,
    assert_trusted_manager_invocation,
    load_backend,
    make_harness,
    record_running,
    sha256,
    xml_count,
    xml_text,
)


@pytest.mark.parametrize("action", ["install", "status", "remove"])
def test_execute_service_dispatches_win32_with_typed_response(
    monkeypatch: pytest.MonkeyPatch,
    action: ServiceAction,
) -> None:
    expected = ServiceCommandResult(
        response=ServiceResponse(
            action=action,
            state="active",
            unit="Proactive MCP Watcher",
            managed=True,
            enabled=True,
            active=True,
            main_pid=PID,
            heartbeat="running",
            linger="not_applicable",
            guidance="none",
            code=None,
        ),
        success=True,
    )
    dispatched: list[ServiceAction] = []

    def execute_task_scheduler(action: ServiceAction) -> ServiceCommandResult:
        dispatched.append(action)
        return expected

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        service_module,
        "execute_task_scheduler",
        execute_task_scheduler,
        raising=False,
    )

    result = service_module.execute_service(action)

    assert result is expected
    assert dispatched == [action]
    assert isinstance(result.response, ServiceResponse)


def test_install_trusted_definition_uses_fixed_current_user_task_and_absolute_powershell() -> (  # noqa: E501
    None
):
    backend = load_backend()
    executable = PureWindowsPath(r"C:\Program Files\Proactive\proactive-mcp.exe")
    database = PureWindowsPath(r"C:\Users\Ada\AppData\Local\Proactive\state.db")

    definition = backend.render_task_definition(executable, database, POWERSHELL)

    assert backend.TASK_NAME == "Proactive MCP Watcher"
    assert backend.MANAGED_TASK_MARKER == "X-Proactive-MCP-Managed=true"
    assert backend.is_managed_task(definition) is True
    unmanaged = definition.replace(backend.MANAGED_TASK_MARKER, "")
    assert backend.is_managed_task(unmanaged) is False
    assert xml_count(definition, "LogonTrigger") == 1
    assert xml_count(definition, "Exec") == 1
    assert xml_text(definition, "LogonType") == "InteractiveToken"
    assert xml_text(definition, "RunLevel") == "LeastPrivilege"
    assert xml_text(definition, "Command") == str(POWERSHELL)
    assert xml_text(definition, "MultipleInstancesPolicy") == "IgnoreNew"
    assert xml_text(definition, "Interval") == "PT1M"
    assert xml_text(definition, "Count") == "3"


@pytest.mark.parametrize(
    ("target", "unsafe"),
    [
        ("executable", PureWindowsPath(r"relative\proactive-mcp.exe")),
        ("database", PureWindowsPath(r"relative\proactive.db")),
        ("database", PureWindowsPath("C:\\state\nbreak.db")),
        ("powershell", PureWindowsPath(r"C:\Users\Public\powershell.exe")),
    ],
)
def test_install_trusted_definition_rejects_non_absolute_or_untrusted_values(
    target: Literal["executable", "database", "powershell"],
    unsafe: PureWindowsPath,
) -> None:
    backend = load_backend()
    executable = PureWindowsPath(r"C:\Proactive\proactive-mcp.exe")
    database = PureWindowsPath(r"C:\Proactive\proactive.db")
    candidates = {
        "executable": (unsafe, database, POWERSHELL),
        "database": (executable, unsafe, POWERSHELL),
        "powershell": (executable, database, unsafe),
    }

    with pytest.raises(ValueError, match=r"absolute|trusted|unsafe|control"):
        _ = backend.render_task_definition(*candidates[target])


def test_install_trusted_definition_encoded_launcher_decodes_absolute_paths_without_shell() -> (  # noqa: E501
    None
):
    backend = load_backend()
    assert_encoded_launcher(backend)


def test_install_trusted_manager_uses_absolute_system_powershell_and_fixed_argv() -> (
    None
):
    backend = load_backend()
    assert_trusted_manager_invocation(backend)


def test_install_trusted_manager_uses_native_system_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_native_system_directory(load_backend(), monkeypatch)


def test_install_trusted_manager_embeds_definition_without_encoded_arguments() -> None:
    assert_register_embeds_definition_in_encoded_command(load_backend())


def test_health_status_reports_enabled_active_running_heartbeat_with_matching_pid(
    tmp_path: Path,
) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    record_running(harness.database)
    harness.manager.stored_definition = backend.render_task_definition(
        harness.executable, harness.database, POWERSHELL
    )
    harness.manager.enabled = True
    harness.manager.active = True

    result = harness.execute(backend, "status")

    assert result.success is True
    assert result.response.state == "active"
    assert result.response.unit == backend.TASK_NAME
    assert result.response.managed is True
    assert result.response.enabled is True
    assert result.response.active is True
    assert result.response.main_pid == PID
    assert harness.manager.main_pid_requests == [PID]
    assert result.response.heartbeat == "running"
    assert result.response.linger == "not_applicable"
    assert result.response.guidance == "none"
    assert result.response.code is None


@pytest.mark.parametrize("failure", ["register", "start", "pid"])
def test_health_rollback_restores_definition_and_prior_active_state_on_pid_heartbeat_failure(  # noqa: E501
    tmp_path: Path,
    failure: Literal["register", "start", "pid"],
) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    record_running(harness.database)
    previous = backend.render_task_definition(
        harness.executable,
        harness.database.with_name("previous.db"),
        POWERSHELL,
    )
    harness.manager.stored_definition = previous
    harness.manager.enabled = True
    harness.manager.active = True
    harness.manager.fail_register_once = failure == "register"
    harness.manager.fail_start_once = failure == "start"
    harness.manager.main_pid_value = PID + (failure == "pid")

    result = harness.execute(backend, "install")

    assert result.success is False
    assert result.response.state == "failed"
    assert result.response.code in {"command_failed", "heartbeat_unavailable"}
    assert harness.manager.stored_definition == previous
    assert harness.manager.enabled is True
    assert harness.manager.active is True


@pytest.mark.parametrize("failure", ["start", "pid"])
def test_health_greenfield_rollback_restores_absent_task_after_failure(
    tmp_path: Path,
    failure: Literal["start", "pid"],
) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    record_running(harness.database)
    harness.manager.fail_start_once = failure == "start"
    harness.manager.main_pid_value = PID + (failure == "pid")

    result = harness.execute(backend, "install")

    assert result.success is False
    assert result.response.state == "failed"
    assert result.response.code in {"command_failed", "heartbeat_unavailable"}
    assert harness.manager.stored_definition is None
    assert harness.manager.enabled is False
    assert harness.manager.active is False


def test_idempotent_managed_install_registers_definition_once(tmp_path: Path) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    record_running(harness.database)

    first = harness.execute(backend, "install")
    installed_definition = harness.manager.stored_definition
    second = harness.execute(backend, "install")

    assert first.success is True
    assert second.success is True
    assert first.response.state == second.response.state == "installed"
    assert harness.manager.stored_definition == installed_definition
    assert harness.manager.operations.count("register") == 1


@pytest.mark.parametrize("action", ["install", "remove"])
def test_unmanaged_collision_install_or_remove_never_mutates(
    tmp_path: Path,
    action: ServiceAction,
) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    foreign = (
        "<Task><RegistrationInfo><Description>foreign</Description>"
        "</RegistrationInfo></Task>"
    )
    harness.manager.stored_definition = foreign
    harness.manager.enabled = True
    harness.manager.active = True

    result = harness.execute(backend, action)

    assert result.success is False
    assert result.response.state == "unmanaged"
    assert result.response.code == "unmanaged_unit"
    assert harness.manager.stored_definition == foreign
    assert harness.manager.enabled is True
    assert harness.manager.active is True
    assert harness.manager.operations == []


def test_idempotent_remove_preserves_profile_canaries(tmp_path: Path) -> None:
    backend = load_backend()
    harness = make_harness(tmp_path)
    record_running(harness.database)
    config = harness.database.with_name("config.toml")
    oauth = harness.database.parent / "credentials" / "google-oauth.json"
    unrelated_task = harness.database.parent / "other-scheduled-task.xml"
    oauth.parent.mkdir()
    _ = config.write_text("config-canary", encoding="utf-8")
    _ = oauth.write_text("oauth-canary", encoding="utf-8")
    _ = unrelated_task.write_text("task-canary", encoding="utf-8")
    canaries = (harness.database, config, oauth, unrelated_task)
    before = {path: sha256(path) for path in canaries}
    harness.manager.stored_definition = backend.render_task_definition(
        harness.executable, harness.database, POWERSHELL
    )
    harness.manager.enabled = True
    harness.manager.active = True

    first = harness.execute(backend, "remove")
    second = harness.execute(backend, "remove")

    assert first.success is True
    assert first.response.state == "removed"
    assert first.response.linger == "not_applicable"
    assert first.response.guidance == "none"
    assert second.success is True
    assert second.response.state == "absent"
    assert harness.manager.operations.count("delete") == 1
    assert {path: sha256(path) for path in canaries} == before
