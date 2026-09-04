"""Transactional artifact and launchd state restoration for macOS services."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING, Protocol

from proactive_mcp.cli.service_darwin_models import (
    ArtifactSnapshot,
    DarwinLayout,
    RestorationPlan,
)
from proactive_mcp.cli.service_launchagent import (
    delete_launch_agent_artifact,
    read_launch_agent_artifact,
    write_launch_agent_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.cli.service_launchd import LaunchdState

__all__ = [
    "DarwinManager",
    "artifact_snapshot",
    "restore_previous",
    "rollback_install",
]


class DarwinManager(Protocol):
    """Launchd operations required by the lifecycle transaction."""

    def state(self) -> LaunchdState:
        """Inspect launchd state."""
        ...

    def enable(self) -> bool:
        """Enable the service."""
        ...

    def disable(self) -> bool:
        """Disable the service."""
        ...

    def bootstrap(self, plist_path: Path) -> bool:
        """Load the service artifact."""
        ...

    def bootout(self) -> bool:
        """Unload the service."""
        ...

    def kickstart(self, *, kill: bool = True) -> bool:
        """Start or restart the service."""
        ...


def artifact_snapshot(path: Path) -> ArtifactSnapshot | None:
    """Capture exact managed-artifact bytes and mode."""
    content = read_launch_agent_artifact(path)
    if content is None:
        return None
    mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    return ArtifactSnapshot(content, mode)


def rollback_install(
    layout: DarwinLayout,
    plan: RestorationPlan,
    manager: DarwinManager,
) -> bool:
    """Best-effort rollback with a truthful exact-state result."""
    unloaded = not plan.unload_candidate or manager.bootout()
    try:
        if plan.previous is None:
            delete_launch_agent_artifact(layout.plist)
        else:
            write_launch_agent_artifact(
                layout.plist,
                plan.previous.content,
                mode=plan.previous.mode,
            )
    except (OSError, ValueError):
        artifact_write_failed = True
    else:
        artifact_write_failed = False
    manager_restored = restore_previous(layout, plan, manager)
    return unloaded and manager_restored and not artifact_write_failed


def restore_previous(
    layout: DarwinLayout,
    plan: RestorationPlan,
    manager: DarwinManager,
) -> bool:
    """Restore manager state only when the exact prior artifact is present."""
    try:
        current = artifact_snapshot(layout.plist)
    except (OSError, ValueError):
        current = None
    if current != plan.previous:
        _ = manager.enable() if plan.previous_state.enabled else manager.disable()
        return False
    return _restore_manager(layout, plan.previous_state, manager)


def _restore_manager(
    layout: DarwinLayout,
    previous: LaunchdState,
    manager: DarwinManager,
) -> bool:
    enabled = manager.enable() if previous.enabled else manager.disable()
    if not enabled or not previous.loaded:
        return enabled
    if not manager.bootstrap(layout.plist):
        return False
    return (
        not previous.active or manager.state().active or manager.kickstart(kill=False)
    )
