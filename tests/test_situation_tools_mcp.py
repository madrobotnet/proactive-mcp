from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from proactive_mcp.server.situation_responses import (
    ListSituationsResponse,
    ProactiveCheckResponse,
)
from tests.memory_tools_stdio import json_text, memory_session
from tests.situation_tool_support import error_text, tool_schema

if TYPE_CHECKING:
    from pathlib import Path

_PRIVATE_MARKER: Final = "PRIVATE-SNOOZE-MARKER"
_TOOL_NAMES: Final = frozenset(
    {
        "proactive_check",
        "confirm_delivery",
        "list_situations",
        "get_situation",
        "acknowledge_situation",
        "snooze_situation",
        "mute_situation",
    }
)


@pytest.mark.anyio
async def test_scheduled_server_exposes_only_unattended_read_tools(
    tmp_path: Path,
) -> None:
    async with memory_session(
        tmp_path,
        server_args=("-m", "proactive_mcp", "serve-scheduled"),
    ) as session:
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}

    assert names == {"confirm_delivery", "get_status", "proactive_check"}


@pytest.mark.anyio
async def test_situation_tools_expose_and_answer_the_m4_surface(tmp_path: Path) -> None:
    # Given: the packaged server running over stdio on an empty database.
    async with memory_session(tmp_path) as session:
        listed = await session.list_tools()

        # When: an agent inspects the surface and calls the delivery tools.
        checked = await session.call_tool("proactive_check")
        pending = await session.call_tool("list_situations", {"state": "pending"})

    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) >= _TOOL_NAMES
    assert tool_schema(tools["proactive_check"]).required == ()
    assert set(tool_schema(tools["confirm_delivery"]).required) >= {"receipt_token"}
    assert set(tool_schema(tools["list_situations"]).properties) >= {
        "after_id",
        "limit",
        "state",
    }
    assert set(tool_schema(tools["get_situation"]).required) >= {"id"}
    assert set(tool_schema(tools["acknowledge_situation"]).required) >= {"id"}
    assert set(tool_schema(tools["snooze_situation"]).required) >= {"id", "until"}
    assert set(tool_schema(tools["mute_situation"]).properties) >= {"id", "scope"}

    # Then: both calls answer with the typed contract and no all-clear.
    response = ProactiveCheckResponse.model_validate_json(json_text(checked))
    assert response.situations == ()
    assert response.all_clear is False
    assert response.warnings
    assert ListSituationsResponse.model_validate_json(json_text(pending)).items == ()


@pytest.mark.anyio
async def test_a_refused_snooze_names_the_argument_without_echoing_it(
    tmp_path: Path,
) -> None:
    # Given: the packaged server running over stdio.
    async with memory_session(tmp_path) as session:
        # When: an agent offers a wake time the tool cannot parse.
        refused = await session.call_tool(
            "snooze_situation", {"id": 1, "until": _PRIVATE_MARKER}
        )

    # Then: the agent learns which argument failed, never its value.
    message = error_text(refused)
    assert "until" in message
    assert _PRIVATE_MARKER not in message


@pytest.mark.anyio
async def test_acknowledging_an_unknown_situation_reports_that_over_stdio(
    tmp_path: Path,
) -> None:
    # Given: the packaged server running over stdio on an empty database.
    async with memory_session(tmp_path) as session:
        # When: an agent acknowledges an id that was never detected.
        refused = await session.call_tool("acknowledge_situation", {"id": 404})

    # Then: the store's refusal survives the tool boundary intact.
    assert "404" in error_text(refused)
