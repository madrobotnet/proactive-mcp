"""Private persistence for the shared read-only Google OAuth credential."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, TypedDict

import keyring as os_keyring
from google.oauth2.credentials import Credentials
from keyring.errors import InitError, KeyringError, NoKeyringError, PasswordDeleteError
from typing_extensions import override

from proactive_mcp.store.private_file import (
    PrivateFileUnsupportedError,
    delete_private_file,
    read_private_text,
    write_private_text,
)
from proactive_mcp.store.storage_errors import UnsafeDatabasePathError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

GOOGLE_READONLY_SCOPES: Final[tuple[str, str]] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)
_KEYRING_SERVICE: Final[str] = "proactive-mcp"
_KEYRING_USERNAME: Final[str] = "google-readonly-oauth"
_CREDENTIAL_FILE_NAME: Final[str] = "google-readonly-oauth.json"
_OAUTH_ENDPOINT: Final[str] = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True, slots=True)
class MissingRefreshTokenError(Exception):
    """Signal that Google authorization did not yield durable credentials."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe operator message."""
        return "Google authorization did not provide a refresh token"


@dataclass(frozen=True, slots=True)
class CredentialScopeError(Exception):
    """Signal that credentials do not have exactly the required scopes."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe operator message."""
        return "Google authorization scopes are not the required read-only scopes"


@dataclass(frozen=True, slots=True)
class CredentialStorageError(Exception):
    """Signal that secure credential storage could not be used."""

    @override
    def __str__(self) -> str:
        """Return a credential-safe operator message."""
        return "Google credential storage is unavailable"


class GoogleCredential(Protocol):
    """Expose the refreshable credential properties used by this package."""

    @property
    def refresh_token(self) -> str | None:
        """Return the durable token needed to refresh access."""
        ...

    @property
    def scopes(self) -> Sequence[str] | None:
        """Return the authorization scopes granted to this credential."""
        ...

    def to_json(self) -> str:
        """Serialize the credential for private persistence."""
        ...


class CredentialKeyring(Protocol):
    """Provide the small OS-keyring capability this store requires."""

    def get_password(self, service_name: str, username: str) -> str | None:
        """Return a stored password, if one exists."""
        ...

    def set_password(self, service_name: str, username: str, password: str) -> None:
        """Persist a password in the OS keyring."""
        ...

    def delete_password(self, service_name: str, username: str) -> None:
        """Remove a password from the OS keyring."""
        ...


class _GoogleAuthorizedUserInfo(TypedDict):
    """The minimum authorized-user shape accepted by google-auth."""

    client_id: str
    client_secret: str
    refresh_token: str
    token: str | None


class _AuthorizedUserWire(BaseModel):
    """Parse credential JSON before it enters google-auth."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    client_id: str
    client_secret: str
    refresh_token: str
    token: str | None = None
    scopes: tuple[str, ...]

    def as_google_authorized_user_info(self) -> _GoogleAuthorizedUserInfo:
        """Return only the trusted fields google-auth needs to refresh."""
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "token": self.token,
        }


_AUTHORIZED_USER_ADAPTER: Final[TypeAdapter[_AuthorizedUserWire]] = TypeAdapter(
    _AuthorizedUserWire
)


@dataclass(frozen=True, slots=True)
class CredentialStore:
    """Store one shared Google credential in the OS keyring or a private fallback."""

    state_directory: Path
    keyring: CredentialKeyring

    def __init__(
        self,
        state_directory: Path,
        *,
        keyring: CredentialKeyring | None = None,
    ) -> None:
        """Bind a state directory and, by default, the platform keyring."""
        object.__setattr__(self, "state_directory", state_directory)
        object.__setattr__(
            self,
            "keyring",
            os_keyring if keyring is None else keyring,
        )

    @property
    def file_path(self) -> Path:
        """Return the private fallback path without creating it."""
        return self.state_directory / "credentials" / _CREDENTIAL_FILE_NAME

    def save(self, credentials: GoogleCredential) -> None:
        """Persist only refreshable credentials with the frozen read-only scopes."""
        _require_refreshable_readonly_credentials(credentials)
        serialized = credentials.to_json()
        try:
            self.keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, serialized)
        except (InitError, NoKeyringError):
            self._write_private_file(serialized)
            return
        except KeyringError as error:
            raise CredentialStorageError from error
        _delete_fallback(self.file_path)

    def load(self) -> GoogleCredential | None:
        """Load valid read-only credentials and treat malformed data as absent."""
        try:
            serialized = self.keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        except (InitError, NoKeyringError):
            serialized = self._read_private_file()
        except KeyringError as error:
            raise CredentialStorageError from error
        if serialized is None:
            serialized = self._read_private_file()
        if serialized is None:
            return None
        return _parse_credentials(serialized)

    def delete(self) -> None:
        """Delete both keyring and fallback copies of the shared credential."""
        try:
            self.keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        except (InitError, NoKeyringError):
            _delete_fallback(self.file_path)
            return
        except PasswordDeleteError as error:
            _delete_fallback(self.file_path)
            raise CredentialStorageError from error
        except KeyringError as error:
            raise CredentialStorageError from error
        _delete_fallback(self.file_path)

    def _write_private_file(self, serialized: str) -> None:
        """Write the keyring-unavailable fallback with owner-only permissions."""
        try:
            write_private_text(self.file_path, serialized)
        except (
            OSError,
            PrivateFileUnsupportedError,
            UnsafeDatabasePathError,
        ) as error:
            raise CredentialStorageError from error

    def _read_private_file(self) -> str | None:
        """Read a regular private fallback file without following symlinks."""
        try:
            return read_private_text(self.file_path)
        except (
            OSError,
            PrivateFileUnsupportedError,
            UnicodeDecodeError,
            UnsafeDatabasePathError,
        ):
            return None


def _require_refreshable_readonly_credentials(credentials: GoogleCredential) -> None:
    """Reject non-durable or over-broad credential material before persistence."""
    if credentials.refresh_token is None or credentials.refresh_token == "":
        raise MissingRefreshTokenError
    if tuple(credentials.scopes or ()) != GOOGLE_READONLY_SCOPES:
        raise CredentialScopeError


def _parse_credentials(serialized: str) -> GoogleCredential | None:
    """Parse an authorized-user JSON string into a refreshable typed credential."""
    try:
        wire = _AUTHORIZED_USER_ADAPTER.validate_json(serialized)
    except ValidationError:
        return None
    if wire.scopes != GOOGLE_READONLY_SCOPES:
        return None
    try:
        credentials = Credentials(
            token=wire.token,
            refresh_token=wire.refresh_token,
            token_uri=_OAUTH_ENDPOINT,
            client_id=wire.client_id,
            client_secret=wire.client_secret,
            scopes=GOOGLE_READONLY_SCOPES,
        )
    except ValueError:
        return None
    try:
        _require_refreshable_readonly_credentials(credentials)
    except (CredentialScopeError, MissingRefreshTokenError):
        return None
    return credentials


def _delete_fallback(path: Path) -> None:
    try:
        delete_private_file(path)
    except PrivateFileUnsupportedError:
        return
    except (
        OSError,
        UnsafeDatabasePathError,
    ) as error:
        raise CredentialStorageError from error


__all__ = [
    "GOOGLE_READONLY_SCOPES",
    "CredentialScopeError",
    "CredentialStorageError",
    "CredentialStore",
    "GoogleCredential",
    "MissingRefreshTokenError",
]
