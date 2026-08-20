"""Official MCP stdio server and status tool."""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict

from proactive_mcp.server.memory_tools import (
    ForgetResponse,
    MemoryItemResponse,
    RecallResponse,
    RememberRequest,
    forget,
    recall,
    remember,
)
from proactive_mcp.store import Store


class DatabaseStatusResponse(BaseModel):
    """Database details exposed by the M0 status contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["healthy"]
    path: str
    journal_mode: str
    busy_timeout: int
    migration_version: int


class GoogleStatusResponse(BaseModel):
    """Google source setup state exposed by the M0 status contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: Literal["not_configured"]
    calendar: Literal["not_configured"]


class DaemonStatusResponse(BaseModel):
    """Daemon state exposed by the M0 status contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["not_running"]


class StatusResponse(BaseModel):
    """Typed status result shared by the CLI and MCP tool."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    overall: Literal["degraded"]
    database: DatabaseStatusResponse
    google: GoogleStatusResponse
    daemon: DaemonStatusResponse
    warnings: tuple[str, ...]


def build_status() -> StatusResponse:
    """Build the current local-only M0 status response."""
    database_path = Path(
        os.environ.get("PROACTIVE_DATABASE", "~/.proactive-mcp/proactive.db")
    )
    with Store(database_path) as store:
        observed = store.status()
    return StatusResponse(
        overall="degraded",
        database=DatabaseStatusResponse(
            status="healthy",
            path=str(observed.path),
            journal_mode=observed.journal_mode,
            busy_timeout=observed.busy_timeout,
            migration_version=observed.migration_version,
        ),
        google=GoogleStatusResponse(
            gmail="not_configured",
            calendar="not_configured",
        ),
        daemon=DaemonStatusResponse(status="not_running"),
        warnings=(
            "Google sources are not configured.",
            "Daemon is not running; status is degraded.",
        ),
    )


async def get_status() -> str:
    """Return current status as an MCP-compatible JSON payload."""
    return build_status().model_dump_json()


def create_server() -> MCPServer[None]:
    """Create the configured proactive-mcp server."""
    server = MCPServer(name="proactive-mcp", version="0.1.0")
    tool = server.tool(
        name="get_status",
        description="Report database, Google source, and daemon status.",
    )
    _ = tool(get_status)

    remember_tool = server.tool(
        name="remember",
        description=(
            "Store a memory when the user mentions dates, appointments, "
            "preferences, or people. For dated facts, set date_anchor to an "
            "ISO date or --MM-DD and use recurrence=yearly when it repeats "
            "annually."
        ),
    )
    _ = remember_tool(remember)

    recall_tool = server.tool(
        name="recall",
        description=(
            "Search stored memories by a substring of entity or content. "
            "Optional kind filter. Does not return archived items."
        ),
    )
    _ = recall_tool(recall)

    forget_tool = server.tool(
        name="forget",
        description=(
            "Archive a memory by id when the user asks to forget or delete it. "
            "This is a soft archive, not a hard delete."
        ),
    )
    _ = forget_tool(forget)

    return server


server = create_server()

__all__ = [
    "DatabaseStatusResponse",
    "ForgetResponse",
    "MemoryItemResponse",
    "RecallResponse",
    "RememberRequest",
    "StatusResponse",
    "build_status",
    "create_server",
    "server",
]
