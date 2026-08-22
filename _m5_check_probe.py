"""Diagnostic: call proactive_check directly and print field shapes only."""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PARAMS = StdioServerParameters(
    command="uv",
    args=[
        "run",
        "--no-sync",
        "--directory",
        "C:/Users/skw05/src/proactive-mcp",
        "proactive-mcp",
        "serve",
    ],
    env={
        **os.environ,
        "PROACTIVE_DATABASE": r"C:\Users\skw05\.proactive-mcp\m5-smoke\proactive.db",
    },
)


async def main() -> None:
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("proactive_check", {})
            data = json.loads(result.content[0].text)
            print("top-level keys:", sorted(data.keys()))
            print("situations len:", len(data.get("situations", [])))
            print("warnings len:", len(data.get("warnings", [])))
            print("warnings:", data.get("warnings"))
            print("held_count:", data.get("held_count"))
            print("all_clear:", data.get("all_clear"))


asyncio.run(main())
