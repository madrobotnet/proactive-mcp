"""Private persistence for the shared read-only Google OAuth credential."""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import keyring as os_keyring
from keyring.errors import InitError, KeyringError, NoKeyringError, PasswordDeleteError

from proactive_mcp.paths import DEFAULT_DATABASE, normalize_state_path

from ._credential_cleanup import (
    canonical_state_root,
    delete_fallback,
    purge_tombstoned,
)
from ._credential_files import (
    read_credential,
    read_state,
    write_credential,
    write_state,
)
from ._credential_legacy import LegacyCredentialAccess, load_legacy
from ._credential_models import (
    GOOGLE_READONLY_SCOPES,
    CredentialEnvelope,
    CredentialKeyring,
    CredentialScopeError,
    CredentialState,
    CredentialStorageError,
    GoogleCredential,
    MissingRefreshTokenError,
    parse_enveloped_credentials,
    require_refreshable_readonly_credentials,
)

for _public_type in (
    MissingRefreshTokenError,
    CredentialScopeError,
    CredentialStorageError,
    GoogleCredential,
    CredentialKeyring,
):
    _public_type.__module__ = __name__
del _public_type

if TYPE_CHECKING:
    from pathlib import Path

_KEYRING_SERVICE: Final[str] = "proactive-mcp"
_KEYRING_USERNAME: Final[str] = "google-readonly-oauth"
_CREDENTIAL_FILE_NAME: Final[str] = "google-readonly-oauth.json"
_CREDENTIAL_STATE_FILE_NAME: Final[str] = "google-readonly-oauth.state.json"
_DEFAULT_STATE_DIRECTORY: Final[Path] = DEFAULT_DATABASE.expanduser().parent
_LEGACY_MIGRATED_MARKER: Final[str] = "proactive-mcp:migrated-to-profile:v1"


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
        object.__setattr__(
            self,
            "state_directory",
            normalize_state_path(state_directory),
        )
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
        canonical = canonical_state_root(self.state_directory)
        profile_id = hashlib.sha256(os.fsencode(canonical)).hexdigest()
        return f"{_KEYRING_USERNAME}:{profile_id}"

    def save(self, credentials: GoogleCredential) -> None:
        """Persist only refreshable credentials with the frozen read-only scopes."""
        require_refreshable_readonly_credentials(credentials)
        state = self._read_state()
        epoch = 1 if state is None else state.epoch + 1
        revision = secrets.token_hex(32)
        serialized = CredentialEnvelope(
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
                CredentialState(
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
            CredentialState(
                epoch=epoch,
                revision=revision,
                backend="keyring",
            )
        )
        delete_fallback(self.file_path)
        object.__setattr__(self, "_loaded_version", (epoch, revision))

    def load(self) -> GoogleCredential | None:
        """Load valid read-only credentials and treat malformed data as absent."""
        state = self._read_state()
        if state is None:
            return load_legacy(
                LegacyCredentialAccess(
                    state_directory=self.state_directory,
                    keyring=self.keyring,
                    keyring_username=self.keyring_username,
                    read_private_file=self._read_private_file,
                    write_state=self._write_state,
                    save=self.save,
                    set_loaded_version=self._set_loaded_version,
                ),
                _DEFAULT_STATE_DIRECTORY,
                delete_fallback,
            )
        object.__setattr__(self, "_loaded_version", (state.epoch, state.revision))
        if state.tombstone or state.backend == "deleted":
            purge_tombstoned(
                self.file_path,
                self.keyring,
                self.keyring_username,
            )
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
        return parse_enveloped_credentials(
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
            CredentialState(
                epoch=epoch,
                revision=revision,
                backend="deleted",
                tombstone=True,
            )
        )
        object.__setattr__(self, "_loaded_version", (epoch, revision))
        try:
            self.keyring.delete_password(_KEYRING_SERVICE, self.keyring_username)
        except (InitError, NoKeyringError) as error:
            delete_fallback(self.file_path)
            raise CredentialStorageError from error
        except PasswordDeleteError as error:
            delete_fallback(self.file_path)
            raise CredentialStorageError from error
        except KeyringError as error:
            raise CredentialStorageError from error
        delete_fallback(self.file_path)

    def _write_private_file(self, serialized: str) -> None:
        write_credential(self.file_path, serialized)

    def _write_state(self, state: CredentialState) -> None:
        write_state(self.state_path, state)

    def _read_private_file(self) -> str | None:
        return read_credential(self.file_path)

    def _read_state(self) -> CredentialState | None:
        return read_state(self.state_path)

    def _load_authoritative_file(
        self,
        state: CredentialState,
    ) -> GoogleCredential | None:
        serialized = self._read_private_file()
        if serialized is None:
            return None
        credentials = parse_enveloped_credentials(
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
            CredentialState(
                epoch=state.epoch,
                revision=state.revision,
                backend="keyring",
            )
        )
        delete_fallback(self.file_path)
        return credentials

    def _set_loaded_version(self, version: tuple[int, str]) -> None:
        object.__setattr__(self, "_loaded_version", version)


__all__ = [
    "GOOGLE_READONLY_SCOPES",
    "CredentialKeyring",
    "CredentialScopeError",
    "CredentialStorageError",
    "CredentialStore",
    "GoogleCredential",
    "MissingRefreshTokenError",
]
