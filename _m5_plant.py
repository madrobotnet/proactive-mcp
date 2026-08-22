"""Diagnostic helper: plant one synthetic memory via the MCP remember tool."""

import asyncio
import json
import os
import sys
from datetime import date, timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TAG = sys.argv[1]
ANCHOR = (date.today() + timedelta(days=3)).strftime("--%m-%d")

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
            result = await session.call_tool(
                "remember",
                {
                    "kind": "fact",
                    "entity": f"스모크{TAG}",
                    "entity_kind": "person",
                    "entity_path": f"테스트/스모크{TAG}",
                    "attribute": "birthday",
                    "content": "스모크 기념일",
                    "date_anchor": ANCHOR,
                    "recurrence": "yearly",
                    "lead_days": 7,
                },
            )
            data = json.loads(result.content[0].text)
            print("memory id:", data["id"])


asyncio.run(main())
