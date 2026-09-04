"""Shared contracts for the Windows Task Scheduler service backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from pathlib import PurePath

TASK_NAME: Final = "Proactive MCP Watcher"
MANAGED_TASK_MARKER: Final = "X-Proactive-MCP-Managed=true"


class TaskSchedulerManager(Protocol):
    """Task Scheduler operations used by the lifecycle transaction."""

    def definition(self) -> str | None:
        """Return exported task XML, or None when the task is absent."""
        ...

    def is_enabled(self) -> bool:
        """Return whether the task is enabled."""
        ...

    def is_active(self) -> bool:
        """Return whether the task is currently running."""
        ...

    def main_pid(self) -> int | None:
        """Return the unique matching daemon descendant PID."""
        ...

    def register(self, definition: str) -> bool:
        """Create or replace the current-user task from XML."""
        ...

    def start(self, ready_file: PurePath | None = None) -> bool:
        """Demand-start the registered task."""
        ...

    def stop(self) -> bool:
        """Stop all running instances of the task."""
        ...

    def delete(self) -> bool:
        """Delete the task from the current-user root folder."""
        ...
