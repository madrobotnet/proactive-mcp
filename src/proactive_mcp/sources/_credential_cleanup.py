"""Credential backend identity and cleanup operations."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from keyring.errors import InitError, KeyringError, NoKeyringError, PasswordDeleteError

from proactive_mcp.store.private_file import (
    PrivateFileUnsupportedError,
    delete_private_file,
)
from proactive_mcp.store.storage_errors import UnsafeDatabasePathError

from ._credential_models import (
    CredentialKeyring,
    CredentialStorageError,
)

if TYPE_CHECKING:
    from pathlib import Path


def canonical_state_root(path: Path) -> str:
    """Return the canonical non-secret state-root identity input."""
    return os.path.normcase(str(path.expanduser().resolve()))


def delete_fallback(path: Path) -> None:
    """Delete one private fallback or expose an unsafe-path failure."""
    try:
        delete_private_file(path)
    except PrivateFileUnsupportedError:
        return
    except (OSError, UnsafeDatabasePathError) as error:
        raise CredentialStorageError from error


def purge_tombstoned(
    path: Path,
    keyring: CredentialKeyring,
    username: str,
) -> None:
    """Remove stale values after a deletion tombstone becomes authoritative."""
    delete_fallback(path)
    try:
        keyring.delete_password("proactive-mcp", username)
    except (InitError, NoKeyringError, PasswordDeleteError):
        return
    except KeyringError as error:
        raise CredentialStorageError from error
