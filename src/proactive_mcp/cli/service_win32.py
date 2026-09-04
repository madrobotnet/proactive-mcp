"""Windows Task Scheduler lifecycle behind the shared service response contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from proactive_mcp.cli.service_task_scheduler import execute_task_scheduler

if TYPE_CHECKING:
    from proactive_mcp.cli.service_models import ServiceAction, ServiceCommandResult

__all__ = ["execute_service"]


def execute_service(action: ServiceAction) -> ServiceCommandResult:
    """Run one Windows Task Scheduler lifecycle operation without presentation I/O."""
    return execute_task_scheduler(action)
