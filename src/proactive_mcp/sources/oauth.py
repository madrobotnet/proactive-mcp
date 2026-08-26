"""Headless-safe installed-app authorization for read-only Google sources."""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from google_auth_oauthlib.flow import WSGITimeoutError
from oauthlib.oauth2 import OAuth2Error
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from requests.exceptions import RequestException
from typing_extensions import override

from proactive_mcp.sources._oauth_flow import (
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_OAUTH_ENDPOINT,
    HEADLESS_AUTHORIZATION_URL_EVENT,
    HEADLESS_SETUP_SUCCESS_EVENT,
    GoogleClientConfig,
    GoogleInstalledAppFlowFactory,
    GoogleInstalledApplicationConfig,
    InstalledAppFlowFactory,
    LocalInstalledAppFlow,
    write_headless_authorization_url,
    write_headless_setup_success,
)
from proactive_mcp.sources._oauth_logging import install_oauth_log_filters
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStore,
    GoogleCredential,
)

GoogleInstalledApplicationConfig.__module__ = __name__
GoogleClientConfig.__module__ = __name__
LocalInstalledAppFlow.__module__ = __name__
InstalledAppFlowFactory.__module__ = __name__

if TYPE_CHECKING:
    from pathlib import Path

_LOOPBACK_HOST: Final[str] = "127.0.0.1"
_LOOPBACK_PORT: Final[int] = 0
_AUTHORIZATION_TIMEOUT_SECONDS: Final[int] = 300
_SUPPORTED_REDIRECT_URIS: Final[tuple[str, ...]] = ("http://127.0.0.1",)

install_oauth_log_filters()


@dataclass(frozen=True, slots=True)
class OAuthClientConfigError(Exception):
    """Signal that an installed-app client JSON file could not be parsed."""

    @override
    def __str__(self) -> str:
        """Return a client-secret-safe operator message."""
        return "Google installed-app client configuration is invalid"


@dataclass(frozen=True, slots=True)
class GoogleOAuthAuthorizationError(Exception):
    """Signal a provider denial or transport failure without provider text."""

    @override
    def __str__(self) -> str:
        """Return a fixed credential-safe authorization message."""
        return "Google authorization failed; run setup again"


@dataclass(frozen=True, slots=True)
class GoogleOAuthAuthorizationTimeoutError(Exception):
    """Signal that the bounded loopback authorization did not complete."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe retry instruction."""
        return "Google authorization timed out; run setup again"


class _InstalledAppClientWire(BaseModel):
    """Parse only client identity from the untrusted bootstrap file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    client_id: str
    client_secret: str

    def as_google_config(self) -> GoogleInstalledApplicationConfig:
        """Build a provider-pinned config for the OAuth library."""
        return {
            "auth_uri": GOOGLE_AUTHORIZATION_ENDPOINT,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uris": list(_SUPPORTED_REDIRECT_URIS),
            "token_uri": GOOGLE_OAUTH_ENDPOINT,
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
            GoogleInstalledAppFlowFactory() if flow_factory is None else flow_factory,
        )

    def authorize(
        self,
        client_secrets_path: Path,
        *,
        reauth: bool = False,
        headless: bool = False,
    ) -> GoogleCredential:
        """Run a bounded loopback flow and persist a durable read-only credential."""
        client_config = _load_client_config(client_secrets_path)
        try:
            flow = self.flow_factory.from_client_config(
                client_config, GOOGLE_READONLY_SCOPES
            )
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
        except (OSError, webbrowser.Error, OAuth2Error, RequestException):
            raise GoogleOAuthAuthorizationError from None
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
    "HEADLESS_AUTHORIZATION_URL_EVENT",
    "HEADLESS_SETUP_SUCCESS_EVENT",
    "GoogleClientConfig",
    "GoogleInstalledApplicationConfig",
    "GoogleOAuthAuthorizationError",
    "GoogleOAuthAuthorizationTimeoutError",
    "GoogleOAuthAuthorizer",
    "InstalledAppFlowFactory",
    "LocalInstalledAppFlow",
    "OAuthClientConfigError",
    "write_headless_authorization_url",
    "write_headless_setup_success",
]
