"""Run the proactive MCP server over stdio."""

from proactive_mcp.server import server

if __name__ == "__main__":
    server.run("stdio")
