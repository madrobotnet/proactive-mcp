"""Command-line interface for proactive-mcp."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Final,
    Literal,
    NoReturn,
    TypeAlias,
    assert_never,
)

from pydantic import BaseModel, ConfigDict

from proactive_mcp.cli.daemon import run_daemon
from proactive_mcp.cli.service import ServiceAction, run_service
from proactive_mcp.config import ConfigError
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server import build_status, create_server, server
from proactive_mcp.server.situation_responses import (
    SourceReadDiagnosticsResponse,
    source_read_diagnostics_response,
)
from proactive_mcp.sources import (
    CredentialScopeError,
    CredentialStorageError,
    GoogleOAuthAuthorizationError,
    GoogleOAuthAuthorizationTimeoutError,
    GoogleReadSmokeDisabledError,
    GoogleReadSummary,
    GoogleSetupOptions,
    MissingGoogleCredentialsError,
    MissingRefreshTokenError,
    OAuthClientConfigError,
    configure_google_sources,
    disconnect_google_sources,
    run_google_read_smoke,
)
from proactive_mcp.store import SourceErrorCode  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Sequence

Command: TypeAlias = Literal[
    "serve",
    "serve-scheduled",
    "status",
    "setup",
    "disconnect",
    "google-smoke",
    "daemon",
    "service",
]
_CLIENT_SECRETS_ENV: Final = "PROACTIVE_GOOGLE_CLIENT_SECRETS"
_GOOGLE_ERRORS: Final = (
    ConfigError,
    CredentialScopeError,
    CredentialStorageError,
    GoogleOAuthAuthorizationError,
    GoogleOAuthAuthorizationTimeoutError,
    GoogleReadSmokeDisabledError,
    MissingGoogleCredentialsError,
    MissingRefreshTokenError,
    OAuthClientConfigError,
)


class _CliArguments(BaseModel):
    """Parse argparse output into the command's typed boundary model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    command: Command
    client_secrets: Path | None = None
    headless: bool = False
    reauth: bool = False
    confirm_real_account_read: bool = False
    once: bool = False
    poll_interval_minutes: float | None = None
    service_action: ServiceAction = "status"


class _GoogleSmokeSourceResponse(BaseModel):
    """PII-free read outcome for one source in smoke command output."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    error_code: SourceErrorCode | None


class _GoogleSmokeGmailResponse(_GoogleSmokeSourceResponse):
    """Legacy Gmail smoke fields plus additive bounded diagnostics."""

    diagnostics: SourceReadDiagnosticsResponse


class _GoogleSmokeResponse(BaseModel):
    """PII-free observable output of an explicitly enabled Google smoke read."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: _GoogleSmokeGmailResponse
    calendar: _GoogleSmokeSourceResponse
    credential_cleanup_failed: bool


class _GoogleDisconnectResponse(BaseModel):
    """Machine-readable confirmation for credential-first rollback."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    google: Literal["disconnected"] = "disconnected"


def run_server() -> None:
    """Run the MCP server over stdio."""
    server.run("stdio")


def run_scheduled_server() -> None:
    """Run the restricted scheduled MCP profile over stdio."""
    create_server(profile="scheduled").run("stdio")


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


def run_disconnect() -> None:
    """Delete stored Google authorization before any state-directory cleanup."""
    disconnect_google_sources(_database_path())
    _ = sys.stdout.write(f"{_GoogleDisconnectResponse().model_dump_json()}\n")


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
        gmail=_GoogleSmokeGmailResponse(
            count=summary.gmail_count,
            error_code=summary.gmail_error_code,
            diagnostics=source_read_diagnostics_response(summary.gmail_diagnostics),
        ),
        calendar=_GoogleSmokeSourceResponse(
            count=summary.calendar_count,
            error_code=summary.calendar_error_code,
        ),
        credential_cleanup_failed=summary.credential_cleanup_failed,
    )


def _database_path() -> Path:
    """Return the expanded local state database path used by all CLI commands."""
    return resolve_paths(os.environ).database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proactive-mcp", description="Local proactive MCP server."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _ = subparsers.add_parser("serve", help="run the MCP server over stdio")
    _ = subparsers.add_parser(
        "serve-scheduled",
        help="run the restricted scheduled MCP profile over stdio",
    )
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
    _ = subparsers.add_parser(
        "disconnect",
        help="delete stored Google authorization before local state cleanup",
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
    daemon = subparsers.add_parser("daemon", help="run the watcher daemon")
    _ = daemon.add_argument(
        "--once", action="store_true", help="run one evaluation pass and exit"
    )
    _ = daemon.add_argument(
        "--poll-interval-minutes",
        type=float,
        metavar="MINUTES",
        help="override the configured watcher poll interval",
    )
    service = subparsers.add_parser("service", help="manage the watcher service")
    service_actions = service.add_subparsers(dest="service_action", required=True)
    for action in ("install", "status", "remove"):
        _ = service_actions.add_parser(action)
    return parser


def _parse_arguments(argv: Sequence[str] | None) -> _CliArguments:
    """Parse command-line input once into the typed command boundary."""
    parsed_argv = sys.argv[1:] if argv is None else argv
    return _CliArguments.model_validate(vars(_parser().parse_args(parsed_argv)))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested CLI command."""
    arguments = _parse_arguments(argv)
    try:
        match arguments.command:
            case "daemon":
                return run_daemon(
                    once=arguments.once,
                    poll_interval_minutes=arguments.poll_interval_minutes,
                )
            case "disconnect":
                run_disconnect()
            case "serve":
                run_server()
            case "serve-scheduled":
                run_scheduled_server()
            case "service":
                return run_service(arguments.service_action)
            case "status":
                _status()
            case "setup":
                run_setup(arguments)
            case "google-smoke":
                run_google_smoke(arguments)
            case _:
                assert_never(arguments.command)
    except _GOOGLE_ERRORS as error:
        _ = sys.stderr.write(f"error: {error}\n")
        return 2
    return 0


def entrypoint() -> NoReturn:
    """Exit the process with the CLI result."""
    raise SystemExit(main())


__all__ = ["entrypoint", "main", "run_scheduled_server", "run_server"]
