"""Headless-safe installed-app authorization for read-only Google sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, TypedDict

from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from typing_extensions import override

from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStore,
    GoogleCredential,
)

if TYPE_CHECKING:
    from pathlib import Path

_LOOPBACK_HOST: Final[str] = "127.0.0.1"
_LOOPBACK_PORT: Final[int] = 0
_AUTHORIZATION_TIMEOUT_SECONDS: Final[int] = 300
_GOOGLE_AUTHORIZATION_ENDPOINT: Final[str] = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_OAUTH_ENDPOINT: Final[str] = "https://oauth2.googleapis.com/token"
_SUPPORTED_REDIRECT_URIS: Final[tuple[str, ...]] = ("http://127.0.0.1",)


@dataclass(frozen=True, slots=True)
class OAuthClientConfigError(Exception):
    """Signal that an installed-app client JSON file could not be parsed."""

    @override
    def __str__(self) -> str:
        """Return a client-secret-safe operator message."""
        return "Google installed-app client configuration is invalid"


@dataclass(frozen=True, slots=True)
class GoogleOAuthAuthorizationTimeoutError(Exception):
    """Signal that the bounded loopback authorization did not complete."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe retry instruction."""
        return "Google authorization timed out; run setup again"


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


class _InstalledAppClientWire(BaseModel):
    """Parse only client identity from the untrusted bootstrap file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    client_id: str
    client_secret: str

    def as_google_config(self) -> GoogleInstalledApplicationConfig:
        """Build a provider-pinned config for the OAuth library."""
        return {
            "auth_uri": _GOOGLE_AUTHORIZATION_ENDPOINT,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uris": list(_SUPPORTED_REDIRECT_URIS),
            "token_uri": _GOOGLE_OAUTH_ENDPOINT,
        }


class _InstalledClientWire(BaseModel):
    """Parse only an installed-app client, never a web-client credential file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    installed: _InstalledAppClientWire

    def as_google_config(self) -> GoogleClientConfig:
        """Produce the strictly typed config shape required by the OAuth library."""
        return {"installed": self.installed.as_google_config()}


_CLIENT_CONFIG_ADAPTER: Final[TypeAdapter[_InstalledClientWire]] = TypeAdapter(
    _InstalledClientWire
)


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
        _ = self.flow.run_local_server(
            host=host,
            port=port,
            open_browser=open_browser,
            timeout_seconds=timeout_seconds,
            prompt=prompt,
        )
        return self.credentials


@dataclass(frozen=True, slots=True)
class _GoogleInstalledAppFlowFactory:
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


@dataclass(frozen=True, slots=True)
class GoogleOAuthAuthorizer:
    """Authorize and persist the shared Gmail and Calendar read-only credential."""

    credential_store: CredentialStore
    flow_factory: InstalledAppFlowFactory

    def __init__(
        self,
        credential_store: CredentialStore,
        *,
        flow_factory: InstalledAppFlowFactory | None = None,
    ) -> None:
        """Bind storage and use the real flow unless a test flow is supplied."""
        object.__setattr__(self, "credential_store", credential_store)
        object.__setattr__(
            self,
            "flow_factory",
            _GoogleInstalledAppFlowFactory() if flow_factory is None else flow_factory,
        )

    def authorize(
        self,
        client_secrets_path: Path,
        *,
        reauth: bool = False,
        headless: bool = False,
    ) -> GoogleCredential:
        """Run a bounded loopback flow and persist a durable read-only credential."""
        flow = self.flow_factory.from_client_config(
            _load_client_config(client_secrets_path), GOOGLE_READONLY_SCOPES
        )
        try:
            if reauth:
                credentials = flow.run_local_server(
                    host=_LOOPBACK_HOST,
                    port=_LOOPBACK_PORT,
                    open_browser=not headless,
                    timeout_seconds=_AUTHORIZATION_TIMEOUT_SECONDS,
                    prompt="consent",
                )
            else:
                credentials = flow.run_local_server(
                    host=_LOOPBACK_HOST,
                    port=_LOOPBACK_PORT,
                    open_browser=not headless,
                    timeout_seconds=_AUTHORIZATION_TIMEOUT_SECONDS,
                )
        except WSGITimeoutError:
            raise GoogleOAuthAuthorizationTimeoutError from None
        self.credential_store.save(credentials)
        return credentials


def _load_client_config(client_secrets_path: Path) -> GoogleClientConfig:
    """Parse an installed-app client file without exposing secret fields in errors."""
    try:
        raw = client_secrets_path.read_bytes()
    except OSError as error:
        raise OAuthClientConfigError from error
    try:
        return _CLIENT_CONFIG_ADAPTER.validate_json(raw).as_google_config()
    except ValidationError as error:
        raise OAuthClientConfigError from error


__all__ = [
    "GoogleClientConfig",
    "GoogleOAuthAuthorizationTimeoutError",
    "GoogleOAuthAuthorizer",
    "InstalledAppFlowFactory",
    "LocalInstalledAppFlow",
    "OAuthClientConfigError",
]
