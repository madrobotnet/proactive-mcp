"""First-party systemd user-service lifecycle for the watcher daemon."""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from proactive_mcp.cli.service_invocation import current_executable
from proactive_mcp.cli.service_models import (
    HeartbeatState,
    LingerState,
    ServiceAction,
    ServiceCode,
    ServiceResponse,
    ServiceState,
)
from proactive_mcp.cli.service_systemd import SystemdUserManager
from proactive_mcp.cli.service_unit import is_managed_unit, render_user_unit
from proactive_mcp.config import ConfigError
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server import build_status
from proactive_mcp.store import UnsafeDatabasePathError

__all__ = ["ServiceAction", "ServiceResponse", "run_service"]

_UNIT_NAME: Final = "proactive-mcp.service"
_MANAGER: Final = SystemdUserManager(_UNIT_NAME)


@dataclass(frozen=True, slots=True)
class _Layout:
    unit: Path
    database: Path


@dataclass(frozen=True, slots=True)
class _Snapshot:
    managed: bool
    enabled: bool
    active: bool
    main_pid: int | None
    heartbeat: HeartbeatState | None
    heartbeat_pid: int | None
    linger: LingerState


@dataclass(frozen=True, slots=True)
class _Outcome:
    action: ServiceAction
    state: ServiceState
    code: ServiceCode | None = None


@dataclass(frozen=True, slots=True)
class _ManagerState:
    enabled: bool
    active: bool


def run_service(action: ServiceAction) -> int:
    """Run one Linux systemd user-service lifecycle operation."""
    if not sys.platform.startswith("linux"):
        return _emit(
            _response(
                _Outcome(action, "unsupported", "unsupported_platform"),
                _empty_snapshot(linger="not_applicable"),
            ),
            success=False,
        )
    try:
        layout = _layout()
        match action:
            case "install":
                response, success = _install(layout)
            case "status":
                response, success = _status(layout)
            case "remove":
                response, success = _remove(layout)
    except OSError:
        response = _result(action, "failed", code="io_failed")
        success = False
    except ValueError:
        response = _result(action, "failed", code="invalid_value")
        success = False
    return _emit(response, success=success)


def _layout() -> _Layout:
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path.home() / ".config" if configured is None else Path(configured)
    return _Layout(
        unit=config_home / "systemd" / "user" / _UNIT_NAME,
        database=resolve_paths(os.environ).database.absolute(),
    )


def _install(layout: _Layout) -> tuple[ServiceResponse, bool]:
    executable = current_executable()
    if executable is None:
        return _result("install", "failed", code="binary_not_found"), False
    previous = layout.unit.read_text(encoding="utf-8") if layout.unit.exists() else None
    if previous is not None and not is_managed_unit(previous):
        return _result("install", "unmanaged", code="unmanaged_unit"), False
    previous_state = _ManagerState(
        enabled=_MANAGER.is_enabled(),
        active=_MANAGER.is_active(),
    )
    layout.unit.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_user_unit(executable, layout.database)
    if previous != rendered:
        _ = layout.unit.write_text(rendered, encoding="utf-8")
    if not (_MANAGER.reload() and _MANAGER.enable() and _MANAGER.start()):
        _rollback_install(layout, previous, previous_state)
        return _result("install", "failed", code="command_failed"), False
    snapshot = _snapshot(layout)
    ready = (
        snapshot.enabled
        and snapshot.active
        and snapshot.heartbeat == "running"
        and snapshot.main_pid == snapshot.heartbeat_pid
    )
    if not ready:
        _rollback_install(layout, previous, previous_state)
        return _result("install", "failed", code="heartbeat_unavailable"), False
    return _response(_Outcome("install", "installed"), snapshot), True


def _status(layout: _Layout) -> tuple[ServiceResponse, bool]:
    if not layout.unit.exists():
        return _result("status", "absent"), True
    if not is_managed_unit(layout.unit.read_text(encoding="utf-8")):
        return _result("status", "unmanaged", code="unmanaged_unit"), False
    snapshot = _snapshot(layout)
    state: ServiceState = "active" if snapshot.active else "inactive"
    healthy = not snapshot.active or (
        snapshot.heartbeat == "running" and snapshot.main_pid == snapshot.heartbeat_pid
    )
    code: ServiceCode | None = None if healthy else "heartbeat_unavailable"
    return _response(_Outcome("status", state, code), snapshot), healthy


def _remove(layout: _Layout) -> tuple[ServiceResponse, bool]:
    if not layout.unit.exists():
        return _result("remove", "absent"), True
    previous = layout.unit.read_text(encoding="utf-8")
    if not is_managed_unit(previous):
        return _result("remove", "unmanaged", code="unmanaged_unit"), False
    previous_state = _ManagerState(
        enabled=_MANAGER.is_enabled(),
        active=_MANAGER.is_active(),
    )
    if not (_MANAGER.stop() and _MANAGER.disable()):
        _restore_service_state(previous_state)
        return _result("remove", "failed", code="command_failed"), False
    layout.unit.unlink()
    if not _MANAGER.reload():
        _ = layout.unit.write_text(previous, encoding="utf-8")
        _restore_service_state(previous_state)
        return _result("remove", "failed", code="command_failed"), False
    return _result("remove", "removed"), True


def _snapshot(layout: _Layout) -> _Snapshot:
    managed = layout.unit.exists() and is_managed_unit(
        layout.unit.read_text(encoding="utf-8")
    )
    enabled = managed and _MANAGER.is_enabled()
    active = managed and _MANAGER.is_active()
    main_pid = _MANAGER.main_pid() if active else None
    heartbeat: HeartbeatState | None = None
    heartbeat_pid: int | None = None
    if active:
        try:
            daemon = build_status().daemon
            heartbeat = daemon.liveness
            heartbeat_pid = daemon.pid
        except (ConfigError, UnsafeDatabasePathError, OSError, sqlite3.Error):
            heartbeat = None
    return _Snapshot(
        managed,
        enabled,
        active,
        main_pid,
        heartbeat,
        heartbeat_pid,
        _MANAGER.linger(),
    )


def _rollback_install(
    layout: _Layout,
    previous: str | None,
    previous_state: _ManagerState,
) -> None:
    _ = _MANAGER.stop()
    _ = _MANAGER.disable()
    if previous is None:
        layout.unit.unlink(missing_ok=True)
    else:
        _ = layout.unit.write_text(previous, encoding="utf-8")
    _ = _MANAGER.reload()
    if previous is not None:
        _restore_service_state(previous_state)


def _restore_service_state(previous_state: _ManagerState) -> None:
    if previous_state.enabled:
        _ = _MANAGER.enable()
    if previous_state.active:
        _ = _MANAGER.start()


def _result(
    action: ServiceAction,
    state: ServiceState,
    *,
    code: ServiceCode | None = None,
) -> ServiceResponse:
    return _response(_Outcome(action, state, code), _empty_snapshot())


def _empty_snapshot(*, linger: LingerState | None = None) -> _Snapshot:
    return _Snapshot(
        managed=False,
        enabled=False,
        active=False,
        main_pid=None,
        heartbeat=None,
        heartbeat_pid=None,
        linger=_MANAGER.linger() if linger is None else linger,
    )


def _response(outcome: _Outcome, snapshot: _Snapshot) -> ServiceResponse:
    return ServiceResponse(
        action=outcome.action,
        state=outcome.state,
        unit=_UNIT_NAME,
        managed=snapshot.managed,
        enabled=snapshot.enabled,
        active=snapshot.active,
        main_pid=snapshot.main_pid,
        heartbeat=snapshot.heartbeat,
        linger=snapshot.linger,
        guidance="enable_linger" if snapshot.linger == "disabled" else "none",
        code=outcome.code,
    )


def _emit(response: ServiceResponse, *, success: bool) -> int:
    _ = sys.stdout.write(f"{response.model_dump_json()}\n")
    return 0 if success else 2
