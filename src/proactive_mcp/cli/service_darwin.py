"""macOS LaunchAgent lifecycle behind the shared service response contract."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Final

from proactive_mcp.cli.service_darwin_models import (
    ArtifactSnapshot,
    DarwinLayout,
    DarwinServiceSnapshot,
    InstallPlan,
    RestorationPlan,
    ServiceOutcome,
    empty_response,
    heartbeat_ready,
    install_ready,
    readiness_failure,
    service_response,
)
from proactive_mcp.cli.service_darwin_transaction import (
    DarwinManager,
    artifact_snapshot,
    restore_previous,
    rollback_install,
)
from proactive_mcp.cli.service_invocation import current_executable
from proactive_mcp.cli.service_launchagent import (
    LAUNCHAGENT_FILENAME,
    LAUNCHAGENT_LABEL,
    delete_launch_agent_artifact,
    is_managed_launch_agent,
    read_launch_agent_artifact,
    render_launch_agent,
    write_launch_agent_artifact,
)
from proactive_mcp.cli.service_launchd import LaunchdState, LaunchdUserManager
from proactive_mcp.cli.service_models import (
    HeartbeatState,
    ServiceAction,
    ServiceCode,
    ServiceCommandResult,
    ServiceResponse,
    ServiceState,
)
from proactive_mcp.config import ConfigError
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server import build_status
from proactive_mcp.store import UnsafeDatabasePathError

__all__ = ["execute_service"]

_READINESS_TIMEOUT_SECONDS: Final = 5.0
_READINESS_INTERVAL_SECONDS: Final = 0.05


_MANAGER: DarwinManager = LaunchdUserManager(LAUNCHAGENT_LABEL)


def execute_service(action: ServiceAction) -> ServiceCommandResult:
    """Run one macOS LaunchAgent lifecycle operation without presentation I/O."""
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
        response = empty_response(ServiceOutcome(action, "failed", "io_failed"))
        success = False
    except ValueError:
        response = empty_response(ServiceOutcome(action, "failed", "invalid_value"))
        success = False
    return ServiceCommandResult(response=response, success=success)


def _layout() -> DarwinLayout:
    return DarwinLayout(
        (Path.home() / "Library" / "LaunchAgents" / LAUNCHAGENT_FILENAME).absolute(),
        resolve_paths(os.environ).database.absolute(),
    )


def _install(layout: DarwinLayout) -> tuple[ServiceResponse, bool]:
    prepared = _prepare_install(layout)
    match prepared:
        case ServiceResponse():
            return prepared, False
        case InstallPlan(
            previous=previous,
            previous_state=previous_state,
            rendered=rendered,
        ):
            pass

    if previous is not None and previous.content == rendered:
        snapshot = _snapshot(layout)
        if install_ready(snapshot):
            response = service_response(
                ServiceOutcome("install", "installed"),
                snapshot,
            )
            return response, True

    if previous_state.loaded and not _MANAGER.bootout():
        return _failure("install", "command_failed")
    candidate, bootstrapped = _replace_and_wait(layout, rendered)
    restoration = RestorationPlan(previous, previous_state, bootstrapped)
    match candidate:
        case DarwinServiceSnapshot():
            snapshot = candidate
        case str(code):
            _ = rollback_install(layout, restoration, _MANAGER)
            return _failure("install", code)

    code = readiness_failure(snapshot)
    if code is not None:
        _ = rollback_install(layout, restoration, _MANAGER)
        return _failure("install", code)
    return service_response(ServiceOutcome("install", "installed"), snapshot), True


def _replace_and_wait(
    layout: DarwinLayout,
    rendered: bytes,
) -> tuple[DarwinServiceSnapshot | ServiceCode, bool]:
    bootstrapped = False
    try:
        layout.plist.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_launch_agent_artifact(layout.plist, rendered)
        if not _MANAGER.enable() or not _MANAGER.bootstrap(layout.plist):
            return "command_failed", bootstrapped
        bootstrapped = True
        if not (_MANAGER.state().active or _MANAGER.kickstart(kill=False)):
            return "command_failed", bootstrapped
        return _wait_for_ready(layout), bootstrapped
    except OSError:
        return "io_failed", bootstrapped
    except ValueError:
        return "invalid_value", bootstrapped


def _prepare_install(layout: DarwinLayout) -> InstallPlan | ServiceResponse:
    executable = current_executable()
    if executable is None:
        return empty_response(ServiceOutcome("install", "failed", "binary_not_found"))
    previous = artifact_snapshot(layout.plist)
    if previous is not None and not is_managed_launch_agent(previous.content):
        return empty_response(ServiceOutcome("install", "unmanaged", "unmanaged_unit"))
    previous_state = _MANAGER.state()
    if previous is None and previous_state.loaded:
        return empty_response(ServiceOutcome("install", "unmanaged", "unmanaged_unit"))
    return InstallPlan(
        previous,
        previous_state,
        render_launch_agent(executable, layout.database),
    )


def _status(layout: DarwinLayout) -> tuple[ServiceResponse, bool]:
    artifact = artifact_snapshot(layout.plist)
    manager_state = _MANAGER.state()
    if artifact is None:
        if manager_state.loaded:
            return _failure("status", "unmanaged_unit", state="unmanaged")
        return empty_response(ServiceOutcome("status", "absent")), True
    if not is_managed_launch_agent(artifact.content):
        return _failure("status", "unmanaged_unit", state="unmanaged")
    snapshot = _snapshot(layout)
    state: ServiceState = "active" if snapshot.active else "inactive"
    healthy = not snapshot.active or heartbeat_ready(snapshot)
    code = None if healthy else "heartbeat_unavailable"
    return service_response(ServiceOutcome("status", state, code), snapshot), healthy


def _remove(layout: DarwinLayout) -> tuple[ServiceResponse, bool]:
    previous = artifact_snapshot(layout.plist)
    previous_state = _MANAGER.state()
    if previous is None:
        return _remove_absent(previous_state)
    if not is_managed_launch_agent(previous.content):
        return _failure("remove", "unmanaged_unit", state="unmanaged")
    return _remove_managed(layout, previous, previous_state)


def _remove_absent(previous_state: LaunchdState) -> tuple[ServiceResponse, bool]:
    if previous_state.loaded:
        return _failure("remove", "unmanaged_unit", state="unmanaged")
    return empty_response(ServiceOutcome("remove", "absent")), True


def _remove_managed(
    layout: DarwinLayout,
    previous: ArtifactSnapshot,
    previous_state: LaunchdState,
) -> tuple[ServiceResponse, bool]:
    if previous_state.loaded and not _MANAGER.bootout():
        return _failure("remove", "command_failed")
    restoration = RestorationPlan(
        previous,
        previous_state,
        unload_candidate=False,
    )
    if not _MANAGER.disable():
        _ = restore_previous(layout, restoration, _MANAGER)
        return _failure("remove", "command_failed")
    try:
        delete_launch_agent_artifact(layout.plist)
    except OSError:
        _ = restore_previous(layout, restoration, _MANAGER)
        return _failure("remove", "io_failed")
    except ValueError:
        _ = restore_previous(layout, restoration, _MANAGER)
        return _failure("remove", "invalid_value")
    return empty_response(ServiceOutcome("remove", "removed")), True


def _snapshot(layout: DarwinLayout) -> DarwinServiceSnapshot:
    content = read_launch_agent_artifact(layout.plist)
    managed = content is not None and is_managed_launch_agent(content)
    state = _MANAGER.state()
    heartbeat: HeartbeatState | None = None
    heartbeat_pid: int | None = None
    if managed and state.active:
        try:
            daemon = build_status().daemon
            heartbeat = daemon.liveness
            heartbeat_pid = daemon.pid
        except (ConfigError, UnsafeDatabasePathError, OSError, sqlite3.Error):
            heartbeat = None
    return DarwinServiceSnapshot(
        managed,
        state.enabled,
        state.loaded,
        state.active,
        state.pid,
        heartbeat,
        heartbeat_pid,
    )


def _wait_for_ready(layout: DarwinLayout) -> DarwinServiceSnapshot:
    deadline = time.monotonic() + _READINESS_TIMEOUT_SECONDS
    snapshot = _snapshot(layout)
    while not install_ready(snapshot) and time.monotonic() < deadline:
        time.sleep(_READINESS_INTERVAL_SECONDS)
        snapshot = _snapshot(layout)
    return snapshot


def _failure(
    action: ServiceAction,
    code: ServiceCode,
    *,
    state: ServiceState = "failed",
) -> tuple[ServiceResponse, bool]:
    return empty_response(ServiceOutcome(action, state, code)), False
