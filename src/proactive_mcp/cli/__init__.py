"""Command-line interface for proactive-mcp."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias

from proactive_mcp.server import build_status, server

if TYPE_CHECKING:
    from collections.abc import Callable

Command: TypeAlias = Literal["serve", "status"]


def run_server() -> None:
    """Run the MCP server over stdio."""
    server.run("stdio")


def _status() -> None:
    _ = sys.stdout.write(f"{build_status().model_dump_json()}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proactive-mcp", description="Local proactive MCP server."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _ = subparsers.add_parser("serve", help="run the MCP server over stdio")
    _ = subparsers.add_parser(
        "status", help="print connection and database status as JSON"
    )
    return parser


def _parse_command(argv: list[str] | None) -> Command:
    parsed_argv = sys.argv[1:] if argv is None else argv
    _ = _parser().parse_args(parsed_argv)
    if parsed_argv == ["serve"]:
        return "serve"
    if parsed_argv == ["status"]:
        return "status"
    raise AssertionError(parsed_argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested CLI command."""
    handlers: dict[Command, Callable[[], None]] = {
        "serve": run_server,
        "status": _status,
    }
    handlers[_parse_command(argv)]()
    return 0


def entrypoint() -> NoReturn:
    """Exit the process with the CLI result."""
    raise SystemExit(main())


__all__ = ["entrypoint", "main", "run_server"]
