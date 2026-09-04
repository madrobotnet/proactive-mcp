"""Internal value objects and response mapping for the Darwin service backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from proactive_mcp.cli.service_launchagent import LAUNCHAGENT_LABEL
from proactive_mcp.cli.service_models import (
    HeartbeatState,
    ServiceAction,
    ServiceCode,
    ServiceResponse,
    ServiceState,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.cli.service_launchd import LaunchdState


@dataclass(frozen=True, slots=True)
class DarwinLayout:
    """Resolved LaunchAgent artifact and profile database paths."""

    plist: Path
    database: Path


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """Exact restorable bytes and mode of a managed plist."""

    content: bytes
    mode: int


@dataclass(frozen=True, slots=True)
class DarwinServiceSnapshot:
    """Launchd and persisted-heartbeat state used for health decisions."""

    managed: bool
    enabled: bool
    loaded: bool
    active: bool
    main_pid: int | None
    heartbeat: HeartbeatState | None
    heartbeat_pid: int | None


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Validated inputs and prior state for one install transaction."""

    previous: ArtifactSnapshot | None
    previous_state: LaunchdState
    rendered: bytes


@dataclass(frozen=True, slots=True)
class RestorationPlan:
    """Prior state plus whether this transaction loaded a candidate service."""

    previous: ArtifactSnapshot | None
    previous_state: LaunchdState
    unload_candidate: bool


@dataclass(frozen=True, slots=True)
class ServiceOutcome:
    """Response fields that are independent of observed service state."""

    action: ServiceAction
    state: ServiceState
    code: ServiceCode | None = None


def heartbeat_ready(snapshot: DarwinServiceSnapshot) -> bool:
    """Return whether the persisted heartbeat belongs to launchd's process."""
    return (
        snapshot.heartbeat == "running"
        and snapshot.main_pid is not None
        and snapshot.main_pid == snapshot.heartbeat_pid
    )


def install_ready(snapshot: DarwinServiceSnapshot) -> bool:
    """Return whether every post-install readiness predicate holds."""
    return (
        snapshot.managed
        and snapshot.enabled
        and snapshot.loaded
        and snapshot.active
        and heartbeat_ready(snapshot)
    )


def readiness_failure(snapshot: DarwinServiceSnapshot) -> ServiceCode | None:
    """Classify manager readiness separately from heartbeat readiness."""
    if not (snapshot.enabled and snapshot.loaded and snapshot.active):
        return "command_failed"
    return None if heartbeat_ready(snapshot) else "heartbeat_unavailable"


def empty_response(outcome: ServiceOutcome) -> ServiceResponse:
    """Build a response with no managed service state."""
    empty = DarwinServiceSnapshot(
        managed=False,
        enabled=False,
        loaded=False,
        active=False,
        main_pid=None,
        heartbeat=None,
        heartbeat_pid=None,
    )
    return service_response(outcome, empty)


def service_response(
    outcome: ServiceOutcome,
    snapshot: DarwinServiceSnapshot,
) -> ServiceResponse:
    """Map internal Darwin state to the shared typed response."""
    return ServiceResponse(
        action=outcome.action,
        state=outcome.state,
        unit=LAUNCHAGENT_LABEL,
        managed=snapshot.managed,
        enabled=snapshot.enabled,
        active=snapshot.active,
        main_pid=snapshot.main_pid,
        heartbeat=snapshot.heartbeat,
        linger="not_applicable",
        guidance="none",
        code=outcome.code,
    )
