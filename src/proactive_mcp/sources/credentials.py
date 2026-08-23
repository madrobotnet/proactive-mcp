"""Private persistence for the shared read-only Google OAuth credential."""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, TypedDict

import keyring as os_keyring
from google.oauth2.credentials import Credentials
from keyring.errors import InitError, KeyringError, NoKeyringError, PasswordDeleteError
from typing_extensions import override

from proactive_mcp.paths import DEFAULT_DATABASE
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
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

GOOGLE_READONLY_SCOPES: Final[tuple[str, str]] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)
_KEYRING_SERVICE: Final[str] = "proactive-mcp"
_KEYRING_USERNAME: Final[str] = "google-readonly-oauth"
_CREDENTIAL_FILE_NAME: Final[str] = "google-readonly-oauth.json"
_CREDENTIAL_STATE_FILE_NAME: Final[str] = "google-readonly-oauth.state.json"
_OAUTH_ENDPOINT: Final[str] = "https://oauth2.googleapis.com/token"
_DEFAULT_STATE_DIRECTORY: Final[Path] = DEFAULT_DATABASE.expanduser().parent
_LEGACY_MIGRATED_MARKER: Final[str] = "proactive-mcp:migrated-to-profile:v1"


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


class _CredentialEnvelope(BaseModel):
    """Version and generation carried with every credential backend value."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    epoch: int = Field(ge=1)
    revision: str = Field(min_length=64, max_length=64)
    credential: str


class _CredentialState(BaseModel):
    """Non-secret authority marker that prevents stale backend resurrection."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    epoch: int = Field(ge=1)
    revision: str = Field(min_length=64, max_length=64)
    backend: Literal["keyring", "file", "deleted"]
    tombstone: bool = False


_ENVELOPE_ADAPTER: Final[TypeAdapter[_CredentialEnvelope]] = TypeAdapter(
    _CredentialEnvelope
)
_STATE_ADAPTER: Final[TypeAdapter[_CredentialState]] = TypeAdapter(_CredentialState)


@dataclass(frozen=True, slots=True)
class CredentialStore:
    """Store one profile-bound Google credential in keyring or private fallback."""

    state_directory: Path
    keyring: CredentialKeyring
    _loaded_version: tuple[int, str] | None

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
        object.__setattr__(self, "_loaded_version", None)

    @property
    def file_path(self) -> Path:
        """Return the private fallback path without creating it."""
        return self.state_directory / "credentials" / _CREDENTIAL_FILE_NAME

    @property
    def state_path(self) -> Path:
        """Return the private non-secret backend authority marker path."""
        return self.state_directory / "credentials" / _CREDENTIAL_STATE_FILE_NAME

    @property
    def keyring_username(self) -> str:
        """Return the non-secret keyring identity for this state root."""
        canonical = _canonical_state_root(self.state_directory)
        profile_id = hashlib.sha256(os.fsencode(canonical)).hexdigest()
        return f"{_KEYRING_USERNAME}:{profile_id}"

    def save(self, credentials: GoogleCredential) -> None:
        """Persist only refreshable credentials with the frozen read-only scopes."""
        _require_refreshable_readonly_credentials(credentials)
        state = self._read_state()
        epoch = 1 if state is None else state.epoch + 1
        revision = secrets.token_hex(32)
        serialized = _CredentialEnvelope(
            epoch=epoch,
            revision=revision,
            credential=credentials.to_json(),
        ).model_dump_json()
        try:
            self.keyring.set_password(
                _KEYRING_SERVICE,
                self.keyring_username,
                serialized,
            )
        except (InitError, NoKeyringError):
            self._write_private_file(serialized)
            self._write_state(
                _CredentialState(
                    epoch=epoch,
                    revision=revision,
                    backend="file",
                )
            )
            object.__setattr__(self, "_loaded_version", (epoch, revision))
            return
        except KeyringError as error:
            raise CredentialStorageError from error
        self._write_state(
            _CredentialState(
                epoch=epoch,
                revision=revision,
                backend="keyring",
            )
        )
        _delete_fallback(self.file_path)
        object.__setattr__(self, "_loaded_version", (epoch, revision))

    def load(self) -> GoogleCredential | None:
        """Load valid read-only credentials and treat malformed data as absent."""
        state = self._read_state()
        if state is None:
            return self._load_legacy()
        object.__setattr__(self, "_loaded_version", (state.epoch, state.revision))
        if state.tombstone or state.backend == "deleted":
            self._purge_tombstoned()
            return None
        if state.backend == "file":
            return self._load_authoritative_file(state)
        try:
            serialized = self.keyring.get_password(
                _KEYRING_SERVICE,
                self.keyring_username,
            )
        except (InitError, NoKeyringError) as error:
            raise CredentialStorageError from error
        except KeyringError as error:
            raise CredentialStorageError from error
        if serialized is None:
            return None
        return _parse_enveloped_credentials(
            serialized,
            state.epoch,
            state.revision,
        )

    def delete(self) -> None:
        """Delete both keyring and fallback copies of the shared credential."""
        state = self._read_state()
        if (
            self._loaded_version is not None
            and state is not None
            and (state.epoch, state.revision) != self._loaded_version
        ):
            return
        epoch = 1 if state is None else state.epoch + 1
        revision = secrets.token_hex(32)
        self._write_state(
            _CredentialState(
                epoch=epoch,
                revision=revision,
                backend="deleted",
                tombstone=True,
            )
        )
        object.__setattr__(self, "_loaded_version", (epoch, revision))
        try:
            self.keyring.delete_password(_KEYRING_SERVICE, self.keyring_username)
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

    def _write_state(self, state: _CredentialState) -> None:
        try:
            write_private_text(self.state_path, state.model_dump_json())
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

    def _read_state(self) -> _CredentialState | None:
        try:
            serialized = read_private_text(self.state_path)
        except (
            OSError,
            PrivateFileUnsupportedError,
            UnicodeDecodeError,
            UnsafeDatabasePathError,
        ):
            return None
        if serialized is None:
            return None
        try:
            return _STATE_ADAPTER.validate_json(serialized)
        except ValidationError:
            return None

    def _load_authoritative_file(
        self,
        state: _CredentialState,
    ) -> GoogleCredential | None:
        serialized = self._read_private_file()
        if serialized is None:
            return None
        credentials = _parse_enveloped_credentials(
            serialized,
            state.epoch,
            state.revision,
        )
        if credentials is None:
            return None
        try:
            self.keyring.set_password(
                _KEYRING_SERVICE,
                self.keyring_username,
                serialized,
            )
        except (InitError, NoKeyringError):
            return credentials
        except KeyringError as error:
            raise CredentialStorageError from error
        self._write_state(
            _CredentialState(
                epoch=state.epoch,
                revision=state.revision,
                backend="keyring",
            )
        )
        _delete_fallback(self.file_path)
        return credentials

    def _load_legacy(self) -> GoogleCredential | None:
        """Migrate one valid single-backend legacy record into an epoch."""
        try:
            serialized = self.keyring.get_password(
                _KEYRING_SERVICE,
                self.keyring_username,
            )
        except (InitError, NoKeyringError):
            return self._adopt_legacy_value(self._read_private_file(), backend="file")
        except KeyringError as error:
            raise CredentialStorageError from error
        if serialized is not None:
            return self._adopt_legacy_value(serialized, backend="keyring")
        if self._is_default_state_root():
            try:
                serialized = self.keyring.get_password(
                    _KEYRING_SERVICE,
                    _KEYRING_USERNAME,
                )
            except (InitError, NoKeyringError) as error:
                raise CredentialStorageError from error
            except KeyringError as error:
                raise CredentialStorageError from error
            if serialized not in (None, _LEGACY_MIGRATED_MARKER):
                credentials = _parse_credentials(serialized)
                if credentials is None:
                    return None
                return self._migrate_global_legacy(serialized, credentials)
        return self._adopt_legacy_value(self._read_private_file(), backend="file")

    def _adopt_legacy_value(
        self,
        serialized: str | None,
        *,
        backend: Literal["keyring", "file"],
    ) -> GoogleCredential | None:
        if serialized is None:
            return None
        envelope = _parse_envelope(serialized)
        if envelope is not None:
            credentials = _parse_credentials(envelope.credential)
            if credentials is None:
                return None
            if backend == "keyring" and self._is_default_state_root():
                self._retire_global_legacy(envelope.credential)
            self._write_state(
                _CredentialState(
                    epoch=envelope.epoch,
                    revision=envelope.revision,
                    backend=backend,
                )
            )
            object.__setattr__(
                self,
                "_loaded_version",
                (envelope.epoch, envelope.revision),
            )
            return credentials
        credentials = _parse_credentials(serialized)
        if credentials is None:
            return None
        self.save(credentials)
        return credentials

    def _migrate_global_legacy(
        self,
        serialized: str,
        credentials: GoogleCredential,
    ) -> GoogleCredential:
        revision = secrets.token_hex(32)
        envelope = _CredentialEnvelope(
            epoch=1,
            revision=revision,
            credential=serialized,
        )
        try:
            self.keyring.set_password(
                _KEYRING_SERVICE,
                self.keyring_username,
                envelope.model_dump_json(),
            )
        except (InitError, NoKeyringError, KeyringError) as error:
            raise CredentialStorageError from error
        self._retire_global_legacy(serialized)
        self._write_state(
            _CredentialState(
                epoch=1,
                revision=revision,
                backend="keyring",
            )
        )
        _delete_fallback(self.file_path)
        object.__setattr__(self, "_loaded_version", (1, revision))
        return credentials

    def _retire_global_legacy(self, expected: str) -> None:
        try:
            current = self.keyring.get_password(
                _KEYRING_SERVICE,
                _KEYRING_USERNAME,
            )
            if current in (None, _LEGACY_MIGRATED_MARKER):
                return
            if current != expected:
                raise CredentialStorageError
            self.keyring.set_password(
                _KEYRING_SERVICE,
                _KEYRING_USERNAME,
                _LEGACY_MIGRATED_MARKER,
            )
        except (InitError, NoKeyringError, KeyringError) as error:
            raise CredentialStorageError from error

    def _is_default_state_root(self) -> bool:
        return _canonical_state_root(self.state_directory) == _canonical_state_root(
            _DEFAULT_STATE_DIRECTORY
        )

    def _purge_tombstoned(self) -> None:
        _delete_fallback(self.file_path)
        try:
            self.keyring.delete_password(_KEYRING_SERVICE, self.keyring_username)
        except (InitError, NoKeyringError, PasswordDeleteError):
            return
        except KeyringError as error:
            raise CredentialStorageError from error


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


def _parse_enveloped_credentials(
    serialized: str,
    expected_epoch: int,
    expected_revision: str,
) -> GoogleCredential | None:
    envelope = _parse_envelope(serialized)
    if envelope is None:
        return None
    if envelope.epoch != expected_epoch or envelope.revision != expected_revision:
        return None
    return _parse_credentials(envelope.credential)


def _parse_envelope(serialized: str) -> _CredentialEnvelope | None:
    try:
        envelope = _ENVELOPE_ADAPTER.validate_json(serialized)
    except ValidationError:
        return None
    return envelope


def _canonical_state_root(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


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
