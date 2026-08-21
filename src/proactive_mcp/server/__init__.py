"""Official MCP stdio server and status tool."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict

from proactive_mcp.clock import UtcClock
from proactive_mcp.server.memory_tools import (
    EntityResponse,
    ForgetResponse,
    ListEntitiesResponse,
    MemoryItemResponse,
    RecallResponse,
    RememberRequest,
    UpdateRequest,
    forget,
    list_entities,
    recall,
    remember,
    update,
)
from proactive_mcp.store import (
    SourceErrorCode,
    SourceFreshness,
    SourceFreshnessStatus,
    SourceSyncState,
    Store,
    evaluate_source_freshness,
)

if TYPE_CHECKING:
    from datetime import datetime


class DatabaseStatusResponse(BaseModel):
    """Database details exposed by the M0 status contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["healthy"]
    path: str
    journal_mode: str
    busy_timeout: int
    migration_version: int


class SourceFreshnessResponse(BaseModel):
    """PII-free, user-visible freshness state for one Google source."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: SourceFreshnessStatus
    last_success_at: str | None
    last_attempt_at: str | None
    age_seconds: int | None
    error_code: SourceErrorCode | None


class GoogleStatusResponse(BaseModel):
    """Google source freshness state exposed by the M2 status contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: SourceFreshnessResponse
    calendar: SourceFreshnessResponse


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
    """Build the current local-only M2 status response."""
    database_path = Path(
        os.environ.get("PROACTIVE_DATABASE", "~/.proactive-mcp/proactive.db")
    ).expanduser()
    now = UtcClock().now()
    with Store(database_path) as store:
        observed = store.status()
        gmail_state, calendar_state = store.list_source_sync()
    gmail = _source_response(gmail_state, now)
    calendar = _source_response(calendar_state, now)
    return StatusResponse(
        overall="degraded",
        database=DatabaseStatusResponse(
            status="healthy",
            path=str(observed.path),
            journal_mode=observed.journal_mode,
            busy_timeout=observed.busy_timeout,
            migration_version=observed.migration_version,
        ),
        google=GoogleStatusResponse(gmail=gmail, calendar=calendar),
        daemon=DaemonStatusResponse(status="not_running"),
        warnings=tuple(
            warning
            for warning in (
                _source_warning("Gmail", gmail.status),
                _source_warning("Calendar", calendar.status),
                "Daemon is not running; status is degraded.",
            )
            if warning is not None
        ),
    )


def _source_response(
    state: SourceSyncState,
    now: datetime,
) -> SourceFreshnessResponse:
    """Serialize persisted freshness without exposing cursors or source data."""
    freshness = evaluate_source_freshness(state, now)
    return _freshness_response(freshness)


def _freshness_response(freshness: SourceFreshness) -> SourceFreshnessResponse:
    """Convert typed freshness timestamps to the public JSON representation."""
    return SourceFreshnessResponse(
        status=freshness.status,
        last_success_at=_timestamp(freshness.last_success_at),
        last_attempt_at=_timestamp(freshness.last_attempt_at),
        age_seconds=freshness.age_seconds,
        error_code=freshness.error_code,
    )


def _timestamp(value: datetime | None) -> str | None:
    """Return an ISO timestamp only when one was persisted."""
    return None if value is None else value.isoformat()


def _source_warning(source: str, status: SourceFreshnessStatus) -> str | None:
    """Return an actionable warning for every source state that is not fresh."""
    template = {
        "ok": "",
        "needs_reauth": (
            "Google {source} requires reauthentication; "
            "run proactive-mcp setup --reauth."
        ),
        "not_configured": (
            "Google {source} is not configured; run proactive-mcp setup."
        ),
        "never_synced": "Google {source} has not completed a read sync.",
        "stale": "Google {source} data is stale.",
        "error": "Google {source} read sync failed.",
    }[status]
    return None if template == "" else template.format(source=source)


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
            "Store a memory from the conversation. Before inventing an entity "
            "path, call list_entities to check existing classifications."
        ),
    )
    _ = remember_tool(remember)

    recall_tool = server.tool(
        name="recall",
        description=(
            "Search active memories by entity, entity alias, path, or content. "
            "Use filters to narrow the newest-first results."
        ),
    )
    _ = recall_tool(recall)

    update_tool = server.tool(
        name="update",
        description=(
            "Replace a memory by id. Before inventing an entity path, call "
            "list_entities to check existing classifications."
        ),
    )
    _ = update_tool(update)

    entities_tool = server.tool(
        name="list_entities",
        description=(
            "List existing active entity classifications before assigning a new "
            "entity path."
        ),
    )
    _ = entities_tool(list_entities)

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
    "EntityResponse",
    "ForgetResponse",
    "ListEntitiesResponse",
    "MemoryItemResponse",
    "RecallResponse",
    "RememberRequest",
    "StatusResponse",
    "UpdateRequest",
    "build_status",
    "create_server",
    "server",
]
