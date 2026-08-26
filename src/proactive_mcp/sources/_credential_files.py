"""Private-file operations for credential backend state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from proactive_mcp.store.private_file import (
    PrivateFileUnsupportedError,
    read_private_text,
    write_private_text,
)
from proactive_mcp.store.storage_errors import UnsafeDatabasePathError

from ._credential_models import (
    STATE_ADAPTER,
    CredentialState,
    CredentialStorageError,
)

if TYPE_CHECKING:
    from pathlib import Path


def write_credential(path: Path, serialized: str) -> None:
    """Write a credential payload through the private-file boundary."""
    try:
        write_private_text(path, serialized)
    except (OSError, PrivateFileUnsupportedError, UnsafeDatabasePathError) as error:
        raise CredentialStorageError from error


def write_state(path: Path, state: CredentialState) -> None:
    """Write the non-secret backend authority marker privately."""
    try:
        write_private_text(path, state.model_dump_json())
    except (OSError, PrivateFileUnsupportedError, UnsafeDatabasePathError) as error:
        raise CredentialStorageError from error


def read_credential(path: Path) -> str | None:
    """Read a regular private fallback without following symlinks."""
    try:
        return read_private_text(path)
    except (
        OSError,
        PrivateFileUnsupportedError,
        UnicodeDecodeError,
        UnsafeDatabasePathError,
    ):
        return None


def read_state(path: Path) -> CredentialState | None:
    """Read and validate the backend authority marker, if usable."""
    try:
        serialized = read_private_text(path)
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
        return STATE_ADAPTER.validate_json(serialized)
    except ValidationError:
        return None
