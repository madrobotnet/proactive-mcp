"""Typed MCP stdio helpers shared by memory tool tests."""

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent


@asynccontextmanager
async def memory_session(tmp_path: Path) -> AsyncGenerator[ClientSession, None]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proactive_mcp.server"],
        env={"PROACTIVE_DATABASE": str(tmp_path / "memory.db")},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        _ = await session.initialize()
        yield session


def json_text(result: CallToolResult) -> str:
    assert result.is_error is False
    assert result.content
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text
