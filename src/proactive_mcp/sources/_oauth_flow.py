"""Typed presentation and adapters for the installed-app OAuth flow."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, TypedDict, final

from google_auth_oauthlib.flow import InstalledAppFlow

if TYPE_CHECKING:
    from collections.abc import Callable

    from proactive_mcp.sources.credentials import GoogleCredential

GOOGLE_AUTHORIZATION_ENDPOINT: Final[str] = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_OAUTH_ENDPOINT: Final[str] = "https://oauth2.googleapis.com/token"
HEADLESS_AUTHORIZATION_URL_EVENT: Final = "oauth.authorization_url"
HEADLESS_SETUP_SUCCESS_EVENT: Final = "Google read-only sources configured."
_BROWSER_AUTHORIZATION_EVENT: Final = (
    "Waiting for Google authorization in your browser."
)


class GoogleInstalledApplicationConfig(TypedDict):
    """Installed-app JSON shape accepted by google-auth-oauthlib."""

    auth_uri: str
    client_id: str
    client_secret: str
    redirect_uris: list[str]
    token_uri: str


class GoogleClientConfig(TypedDict):
    """Top-level installed-app JSON shape accepted by google-auth-oauthlib."""

    installed: GoogleInstalledApplicationConfig


class LocalInstalledAppFlow(Protocol):
    """Run the only interactive OAuth capability setup needs."""

    @property
    def credentials(self) -> GoogleCredential:
        """Return credentials issued after successful authorization."""
        ...

    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        prompt: str | None = None,
    ) -> GoogleCredential:
        """Run a bounded loopback authorization server."""
        ...


class InstalledAppFlowFactory(Protocol):
    """Create an installed-app flow from a parsed client configuration."""

    def from_client_config(
        self,
        client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> LocalInstalledAppFlow:
        """Create a flow that requests only the provided scopes."""
        ...


def write_headless_authorization_url(url: str) -> None:
    """Emit the single owned authorization URL event."""
    if not url.startswith(GOOGLE_AUTHORIZATION_ENDPOINT):
        return
    _ = sys.stdout.write(f"{HEADLESS_AUTHORIZATION_URL_EVENT} {url}\n")


def write_headless_setup_success() -> None:
    """Emit the single owned setup success event."""
    _ = sys.stdout.write(f"{HEADLESS_SETUP_SUCCESS_EVENT}\n")


def _write_browser_authorization_status() -> None:
    """Emit a fixed status without the browser authorization URL or state."""
    _ = sys.stdout.write(f"{_BROWSER_AUTHORIZATION_EVENT}\n")


@final
class _OwnedAuthorizationPrompt:
    """Capture oauthlib's URL once so this package owns presentation.

    Mutation is required because oauthlib calls format() for both its logger
    and its print.
    """

    __slots__ = ("_emitted", "_headless")
    _emitted: bool
    _headless: bool

    def __init__(self, *, headless: bool) -> None:
        self._emitted = False
        self._headless = headless

    def format(self, **kwargs: str) -> str:
        if not self._emitted:
            if self._headless:
                write_headless_authorization_url(kwargs["url"])
            else:
                _write_browser_authorization_status()
            self._emitted = True
        return ""


@dataclass(frozen=True, slots=True)
class _GoogleLocalInstalledAppFlow:
    """Adapt the untyped oauthlib flow to the local typed flow contract."""

    flow: InstalledAppFlow

    @property
    def credentials(self) -> GoogleCredential:
        """Return OAuth credentials after oauthlib completes authorization."""
        return self.flow.credentials

    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        prompt: str | None = None,
    ) -> GoogleCredential:
        """Run the oauthlib loopback server with the bounded setup options."""
        runner: Callable[..., GoogleCredential] = self.flow.run_local_server
        return runner(
            host=host,
            port=port,
            open_browser=open_browser,
            timeout_seconds=timeout_seconds,
            prompt=prompt,
            authorization_prompt_message=_OwnedAuthorizationPrompt(
                headless=not open_browser
            ),
        )


@dataclass(frozen=True, slots=True)
class GoogleInstalledAppFlowFactory:
    """Adapt google-auth-oauthlib to the narrow flow factory contract."""

    def from_client_config(
        self,
        client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> LocalInstalledAppFlow:
        """Create a real installed-app flow with the exact readonly scopes."""
        return _GoogleLocalInstalledAppFlow(
            InstalledAppFlow.from_client_config(client_config, scopes=scopes)
        )
