from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import anyio

from proactive_mcp.store import Store
from tests.memory_tools_stdio import memory_session
from tests.situation_tool_support import pending_detection, write_config

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from mcp import ClientSession
    from mcp.types import Tool

DAILY_TOOLS: Final = frozenset(
    {
        "acknowledge_situation",
        "confirm_delivery",
        "forget",
        "get_situation",
        "get_status",
        "list_entities",
        "list_situations",
        "mute_situation",
        "proactive_check",
        "recall",
        "remember",
        "snooze_situation",
        "update",
    }
)
SCHEDULED_TOOLS: Final = frozenset(
    {"confirm_delivery", "get_status", "proactive_check"}
)
SERVE: Final = ("-m", "proactive_mcp", "serve")
SCHEDULED: Final = ("-m", "proactive_mcp", "serve-scheduled")
_TIMEOUT_SECONDS: Final = 20
_DATABASE_NAME: Final = "memory.db"


@dataclass(frozen=True, slots=True)
class DeliveryCounts:
    pending: int
    delivered: int
    events: int


@asynccontextmanager
async def session(
    tmp_path: Path,
    server_args: tuple[str, ...],
) -> AsyncGenerator[ClientSession, None]:
    with anyio.fail_after(_TIMEOUT_SECONDS):
        async with memory_session(tmp_path, server_args=server_args) as client_session:
            yield client_session


async def listed_tools(
    tmp_path: Path,
    server_args: tuple[str, ...],
) -> dict[str, Tool]:
    async with session(tmp_path, server_args) as client_session:
        listed = await client_session.list_tools()
    return {tool.name: tool for tool in listed.tools}


def delivery_counts(tmp_path: Path) -> DeliveryCounts:
    with Store(tmp_path / _DATABASE_NAME) as store:
        return DeliveryCounts(
            pending=store.situations.count_situations("pending"),
            delivered=store.situations.count_situations("delivered"),
            events=store.situations.count_deliveries(),
        )


def seed_critical_conflict(tmp_path: Path) -> None:
    write_config(tmp_path, quiet_hours_start="00:00", quiet_hours_end="00:00")
    with Store(tmp_path / _DATABASE_NAME) as store:
        _ = store.situations.upsert_detections(
            (pending_detection("session-contract", priority="critical"),)
        )
