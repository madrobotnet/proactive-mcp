"""Adoption and retirement of pre-authority credential records."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from keyring.errors import InitError, KeyringError, NoKeyringError

from ._credential_models import (
    CredentialEnvelope,
    CredentialKeyring,
    CredentialState,
    CredentialStorageError,
    GoogleCredential,
    parse_credentials,
    parse_envelope,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_KEYRING_SERVICE = "proactive-mcp"
_KEYRING_USERNAME = "google-readonly-oauth"
_LEGACY_MIGRATED_MARKER = "proactive-mcp:migrated-to-profile:v1"


@dataclass(frozen=True, slots=True)
class LegacyCredentialAccess:
    """Capabilities needed to adopt a credential from the legacy layout."""

    state_directory: Path
    keyring: CredentialKeyring
    keyring_username: str
    read_private_file: Callable[[], str | None]
    write_state: Callable[[CredentialState], None]
    save: Callable[[GoogleCredential], None]
    set_loaded_version: Callable[[tuple[int, str]], None]


def load_legacy(
    access: LegacyCredentialAccess,
    default_state_directory: Path,
    delete_fallback: Callable[[Path], None],
) -> GoogleCredential | None:
    """Load and adopt one valid record from the previous backend layout."""
    try:
        serialized = access.keyring.get_password(
            _KEYRING_SERVICE,
            access.keyring_username,
        )
    except (InitError, NoKeyringError):
        return _adopt(
            access,
            access.read_private_file(),
            backend="file",
            retire_global=False,
        )
    except KeyringError as error:
        raise CredentialStorageError from error
    if serialized is not None:
        return _adopt(
            access,
            serialized,
            backend="keyring",
            retire_global=_is_default(
                access.state_directory,
                default_state_directory,
            ),
        )
    if _is_default(access.state_directory, default_state_directory):
        try:
            serialized = access.keyring.get_password(
                _KEYRING_SERVICE,
                _KEYRING_USERNAME,
            )
        except (InitError, NoKeyringError) as error:
            raise CredentialStorageError from error
        except KeyringError as error:
            raise CredentialStorageError from error
        if serialized not in (None, _LEGACY_MIGRATED_MARKER):
            credentials = parse_credentials(serialized)
            if credentials is None:
                return None
            return _migrate_global(access, serialized, credentials, delete_fallback)
    return _adopt(
        access,
        access.read_private_file(),
        backend="file",
        retire_global=False,
    )


def _adopt(
    access: LegacyCredentialAccess,
    serialized: str | None,
    *,
    backend: Literal["keyring", "file"],
    retire_global: bool,
) -> GoogleCredential | None:
    if serialized is None:
        return None
    envelope = parse_envelope(serialized)
    if envelope is not None:
        credentials = parse_credentials(envelope.credential)
        if credentials is None:
            return None
        if retire_global:
            _retire_global(access, envelope.credential)
        access.write_state(
            CredentialState(
                epoch=envelope.epoch,
                revision=envelope.revision,
                backend=backend,
            )
        )
        access.set_loaded_version((envelope.epoch, envelope.revision))
        return credentials
    credentials = parse_credentials(serialized)
    if credentials is None:
        return None
    access.save(credentials)
    return credentials


def _migrate_global(
    access: LegacyCredentialAccess,
    serialized: str,
    credentials: GoogleCredential,
    delete_fallback: Callable[[Path], None],
) -> GoogleCredential:
    revision = secrets.token_hex(32)
    envelope = CredentialEnvelope(epoch=1, revision=revision, credential=serialized)
    try:
        access.keyring.set_password(
            _KEYRING_SERVICE,
            access.keyring_username,
            envelope.model_dump_json(),
        )
    except (InitError, NoKeyringError, KeyringError) as error:
        raise CredentialStorageError from error
    _retire_global(access, serialized)
    access.write_state(CredentialState(epoch=1, revision=revision, backend="keyring"))
    delete_fallback(access.state_directory / "credentials/google-readonly-oauth.json")
    access.set_loaded_version((1, revision))
    return credentials


def _retire_global(access: LegacyCredentialAccess, expected: str) -> None:
    try:
        current = access.keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if current in (None, _LEGACY_MIGRATED_MARKER):
            return
        if current != expected:
            raise CredentialStorageError
        access.keyring.set_password(
            _KEYRING_SERVICE,
            _KEYRING_USERNAME,
            _LEGACY_MIGRATED_MARKER,
        )
    except (InitError, NoKeyringError, KeyringError) as error:
        raise CredentialStorageError from error


def _is_default(path: Path, default: Path) -> bool:
    canonical = os.path.normcase(str(path.expanduser().resolve()))
    expected = os.path.normcase(str(default.expanduser().resolve()))
    return canonical == expected
