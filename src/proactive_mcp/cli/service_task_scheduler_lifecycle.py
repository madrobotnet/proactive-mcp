"""Transactional lifecycle for the Windows Task Scheduler watcher task."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import PurePath

from proactive_mcp.cli.service_invocation import current_executable
from proactive_mcp.cli.service_models import (
    HeartbeatState,
    ServiceAction,
    ServiceCode,
    ServiceCommandResult,
    ServiceResponse,
    ServiceState,
)
from proactive_mcp.cli.service_task_scheduler_contract import (
    MANAGED_TASK_MARKER,
    TASK_NAME,
    TaskSchedulerManager,
)
from proactive_mcp.config import ConfigError
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server import build_status
from proactive_mcp.store import UnsafeDatabasePathError


@dataclass(frozen=True, slots=True)
class _Snapshot:
    managed: bool
    enabled: bool
    active: bool
    main_pid: int | None
    heartbeat: HeartbeatState | None
    heartbeat_pid: int | None


@dataclass(frozen=True, slots=True)
class _Previous:
    definition: str | None
    enabled: bool
    active: bool


@dataclass(frozen=True, slots=True)
class _Outcome:
    action: ServiceAction
    state: ServiceState
    code: ServiceCode | None = None


def execute_task_scheduler_lifecycle(
    action: ServiceAction,
    manager: TaskSchedulerManager,
    render_definition: Callable[[PurePath, PurePath], str],
) -> ServiceCommandResult:
    """Execute one Task Scheduler lifecycle operation."""
    try:
        match action:
            case "install":
                response, success = _install(manager, render_definition)
            case "status":
                response, success = _status(manager)
            case "remove":
                response, success = _remove(manager)
    except OSError:
        response, success = _result(action, "failed", "io_failed"), False
    except ValueError:
        response, success = _result(action, "failed", "invalid_value"), False
    return ServiceCommandResult(response=response, success=success)


def _install(
    manager: TaskSchedulerManager,
    render_definition: Callable[[PurePath, PurePath], str],
) -> tuple[ServiceResponse, bool]:
    executable = current_executable()
    if executable is None:
        return _result("install", "failed", "binary_not_found"), False
    database = resolve_paths(os.environ).database.absolute()
    ready_file = database.with_name(f"{database.name}.service-ready")
    rendered = render_definition(executable, database)
    previous_definition = manager.definition()
    if previous_definition is not None and not _is_managed_task_definition(
        previous_definition
    ):
        return _result("install", "unmanaged", "unmanaged_unit"), False
    previous = _Previous(
        previous_definition,
        manager.is_enabled() if previous_definition is not None else False,
        manager.is_active() if previous_definition is not None else False,
    )
    if previous_definition == rendered and previous.enabled and previous.active:
        snapshot = _snapshot(manager, rendered)
        if _ready(snapshot):
            return _response(_Outcome("install", "installed"), snapshot), True
    changed = previous_definition != rendered or not previous.enabled
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file.unlink(missing_ok=True)
    try:
        command_succeeded = (not changed or manager.register(rendered)) and (
            not (changed or not previous.active) or manager.start(ready_file)
        )
    finally:
        ready_file.unlink(missing_ok=True)
    if not command_succeeded:
        _rollback(manager, previous)
        return _result("install", "failed", "command_failed"), False
    snapshot = _snapshot(manager, rendered)
    if not _ready(snapshot):
        _rollback(manager, previous)
        return _result("install", "failed", "heartbeat_unavailable"), False
    return _response(_Outcome("install", "installed"), snapshot), True


def _status(manager: TaskSchedulerManager) -> tuple[ServiceResponse, bool]:
    definition = manager.definition()
    if definition is None:
        return _result("status", "absent"), True
    if not _is_managed_task_definition(definition):
        return _result("status", "unmanaged", "unmanaged_unit"), False
    snapshot = _snapshot(manager, definition)
    state: ServiceState = "active" if snapshot.active else "inactive"
    healthy = not snapshot.active or (
        snapshot.heartbeat == "running" and snapshot.main_pid == snapshot.heartbeat_pid
    )
    code: ServiceCode | None = None if healthy else "heartbeat_unavailable"
    return _response(_Outcome("status", state, code), snapshot), healthy


def _remove(manager: TaskSchedulerManager) -> tuple[ServiceResponse, bool]:
    definition = manager.definition()
    if definition is None:
        return _result("remove", "absent"), True
    if not _is_managed_task_definition(definition):
        return _result("remove", "unmanaged", "unmanaged_unit"), False
    previous = _Previous(definition, manager.is_enabled(), manager.is_active())
    if previous.active and not manager.stop():
        _rollback(manager, previous)
        return _result("remove", "failed", "command_failed"), False
    if not manager.delete():
        _rollback(manager, previous)
        return _result("remove", "failed", "command_failed"), False
    return _result("remove", "removed"), True


def _snapshot(manager: TaskSchedulerManager, definition: str) -> _Snapshot:
    managed = _is_managed_task_definition(definition)
    enabled = managed and manager.is_enabled()
    active = managed and manager.is_active()
    main_pid: int | None = None
    heartbeat: HeartbeatState | None = None
    heartbeat_pid: int | None = None
    if active:
        try:
            daemon = build_status().daemon
            heartbeat = daemon.liveness
            heartbeat_pid = daemon.pid
            if heartbeat_pid is not None:
                main_pid = manager.main_pid(heartbeat_pid)
        except (ConfigError, UnsafeDatabasePathError, OSError, sqlite3.Error):
            heartbeat = None
    return _Snapshot(managed, enabled, active, main_pid, heartbeat, heartbeat_pid)


def _ready(snapshot: _Snapshot) -> bool:
    return (
        snapshot.enabled
        and snapshot.active
        and snapshot.heartbeat == "running"
        and snapshot.main_pid == snapshot.heartbeat_pid
    )


def _rollback(manager: TaskSchedulerManager, previous: _Previous) -> None:
    _ = manager.stop()
    if previous.definition is None:
        _ = manager.delete()
        return
    _ = manager.register(previous.definition)
    if previous.active:
        _ = manager.start()


def _result(
    action: ServiceAction,
    state: ServiceState,
    code: ServiceCode | None = None,
) -> ServiceResponse:
    return _response(_Outcome(action, state, code), _empty_snapshot())


def _empty_snapshot() -> _Snapshot:
    return _Snapshot(
        managed=False,
        enabled=False,
        active=False,
        main_pid=None,
        heartbeat=None,
        heartbeat_pid=None,
    )


def _response(outcome: _Outcome, snapshot: _Snapshot) -> ServiceResponse:
    return ServiceResponse(
        action=outcome.action,
        state=outcome.state,
        unit=TASK_NAME,
        managed=snapshot.managed,
        enabled=snapshot.enabled,
        active=snapshot.active,
        main_pid=snapshot.main_pid,
        heartbeat=snapshot.heartbeat,
        linger="not_applicable",
        guidance="none",
        code=outcome.code,
    )


def _is_managed_task_definition(definition: str) -> bool:
    """Return whether XML has an exact managed Documentation element."""
    try:
        root = ET.fromstring(definition)  # noqa: S314
    except ET.ParseError:
        return False
    return any(
        child.tag.rpartition("}")[2] == "Documentation"
        and (child.text or "").strip() == MANAGED_TASK_MARKER
        for registration in root.iter()
        if registration.tag.rpartition("}")[2] == "RegistrationInfo"
        for child in registration
    )
