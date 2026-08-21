"""Command-line interface for proactive-mcp."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, NoReturn, TypeAlias

from pydantic import BaseModel, ConfigDict

from proactive_mcp.server import build_status, server
from proactive_mcp.sources import (
    CredentialScopeError,
    CredentialStorageError,
    GoogleOAuthAuthorizationTimeoutError,
    GoogleReadSmokeDisabledError,
    GoogleReadSummary,
    GoogleSetupOptions,
    MissingGoogleCredentialsError,
    MissingRefreshTokenError,
    OAuthClientConfigError,
    configure_google_sources,
    run_google_read_smoke,
)
from proactive_mcp.store import (
    SourceErrorCode,  # noqa: TC001 - Pydantic resolves this annotation at runtime.
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

Command: TypeAlias = Literal["serve", "status", "setup", "google-smoke"]

_DEFAULT_DATABASE_PATH = Path("~/.proactive-mcp/proactive.db")
_CLIENT_SECRETS_ENV = "PROACTIVE_GOOGLE_CLIENT_SECRETS"


class _CliArguments(BaseModel):
    """Parse argparse output into the command's typed boundary model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    command: Command
    client_secrets: Path | None = None
    headless: bool = False
    reauth: bool = False
    confirm_real_account_read: bool = False


class _GoogleSmokeSourceResponse(BaseModel):
    """PII-free read outcome for one source in smoke command output."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    error_code: SourceErrorCode | None


class _GoogleSmokeResponse(BaseModel):
    """PII-free observable output of an explicitly enabled Google smoke read."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: _GoogleSmokeSourceResponse
    calendar: _GoogleSmokeSourceResponse
    credential_cleanup_failed: bool


def run_server() -> None:
    """Run the MCP server over stdio."""
    server.run("stdio")


def _status() -> None:
    _ = sys.stdout.write(f"{build_status().model_dump_json()}\n")


def run_setup(arguments: _CliArguments) -> None:
    """Authorize and configure the Gmail and Calendar read-only sources."""
    client_secrets_path = arguments.client_secrets
    if client_secrets_path is None:
        configured_path = os.environ.get(_CLIENT_SECRETS_ENV)
        client_secrets_path = (
            Path(configured_path)
            if configured_path is not None
            else _database_path().parent / "client_secret.json"
        )
    configure_google_sources(
        _database_path(),
        GoogleSetupOptions(
            client_secrets_path=client_secrets_path,
            reauth=arguments.reauth,
            headless=arguments.headless,
        ),
    )
    _ = sys.stdout.write("Google read-only sources configured.\n")


def run_google_smoke(arguments: _CliArguments) -> None:
    """Run the explicitly confirmed real-account Gmail and Calendar smoke read."""
    summary = run_google_read_smoke(
        _database_path(),
        enabled=arguments.confirm_real_account_read,
    )
    _ = sys.stdout.write(f"{_smoke_response(summary).model_dump_json()}\n")


def _smoke_response(summary: GoogleReadSummary) -> _GoogleSmokeResponse:
    """Serialize only redacted source counts and normalized error codes."""
    return _GoogleSmokeResponse(
        gmail=_GoogleSmokeSourceResponse(
            count=summary.gmail_count,
            error_code=summary.gmail_error_code,
        ),
        calendar=_GoogleSmokeSourceResponse(
            count=summary.calendar_count,
            error_code=summary.calendar_error_code,
        ),
        credential_cleanup_failed=summary.credential_cleanup_failed,
    )


def _database_path() -> Path:
    """Return the expanded local state database path used by all CLI commands."""
    configured_path = os.environ.get("PROACTIVE_DATABASE", _DEFAULT_DATABASE_PATH)
    return Path(configured_path).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proactive-mcp", description="Local proactive MCP server."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _ = subparsers.add_parser("serve", help="run the MCP server over stdio")
    _ = subparsers.add_parser(
        "status", help="print connection and database status as JSON"
    )
    setup = subparsers.add_parser("setup", help="connect read-only Google sources")
    _ = setup.add_argument(
        "--reauth",
        action="store_true",
        help="replace the current Google authorization",
    )
    _ = setup.add_argument(
        "--headless",
        action="store_true",
        help="do not launch a browser for loopback authorization",
    )
    _ = setup.add_argument(
        "--client-secrets",
        metavar="PATH",
        help="path to an installed-app OAuth client secret file",
    )
    smoke = subparsers.add_parser(
        "google-smoke",
        help="perform an explicitly confirmed real-account read-only Google smoke test",
    )
    _ = smoke.add_argument(
        "--confirm-real-account-read",
        action="store_true",
        help=(
            "confirm that this command may read the configured Gmail and Calendar "
            "account"
        ),
    )
    return parser


def _parse_arguments(argv: Sequence[str] | None) -> _CliArguments:
    """Parse command-line input once into the typed command boundary."""
    parsed_argv = sys.argv[1:] if argv is None else argv
    return _CliArguments.model_validate(vars(_parser().parse_args(parsed_argv)))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested CLI command."""
    arguments = _parse_arguments(argv)
    try:
        handlers: dict[Command, Callable[[], None]] = {
            "serve": run_server,
            "status": _status,
            "setup": lambda: run_setup(arguments),
            "google-smoke": lambda: run_google_smoke(arguments),
        }
        handlers[arguments.command]()
    except (
        CredentialScopeError,
        CredentialStorageError,
        GoogleOAuthAuthorizationTimeoutError,
        GoogleReadSmokeDisabledError,
        MissingGoogleCredentialsError,
        MissingRefreshTokenError,
        OAuthClientConfigError,
    ) as error:
        _ = sys.stderr.write(f"error: {error}\n")
        return 2
    return 0


def entrypoint() -> NoReturn:
    """Exit the process with the CLI result."""
    raise SystemExit(main())


__all__ = ["entrypoint", "main", "run_server"]
