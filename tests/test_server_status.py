import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

from proactive_mcp.server import StatusResponse


@pytest.mark.anyio
async def test_get_status_over_stdio(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proactive_mcp.server"],
        env={"PROACTIVE_DATABASE": str(tmp_path / "status.db")},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        _ = await session.initialize()
        result = await session.call_tool("get_status")

    content = result.content[0]
    assert isinstance(content, TextContent)
    status = StatusResponse.model_validate_json(content.text)
    assert status.database.status == "healthy"
    assert status.google.gmail == "not_configured"
    assert status.google.calendar == "not_configured"
    assert status.daemon.status == "not_running"
    assert status.overall == "degraded"
    assert status.warnings
    assert "all-clear" not in status.model_dump_json().lower()
