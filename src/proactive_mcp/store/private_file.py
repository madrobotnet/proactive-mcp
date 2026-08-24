"""Descriptor-pinned private text files for credential fallback storage."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from typing_extensions import override

from .private_path import open_private_parent, prepare_private_database_file
from .storage_errors import UnsafeDatabasePathError

if TYPE_CHECKING:
    from pathlib import Path

_PRIVATE_FILE_MODE: Final[int] = 0o600


@dataclass(frozen=True, slots=True)
class PrivateFileUnsupportedError(Exception):
    """Signal that private fallback files are unavailable on this platform."""

    path: Path

    @override
    def __str__(self) -> str:
        """Return a path-free platform capability message."""
        return "private fallback files are unavailable on this platform"


def write_private_text(path: Path, content: str) -> None:
    """Atomically replace a private POSIX text file through a pinned parent."""
    if os.name == "nt":
        _write_windows_private_text(path, content)
        return
    directory_fd = _open_posix_parent(path)
    temporary_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        descriptor = _open_regular(
            directory_fd,
            temporary_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            path,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as private_file:
            _ = private_file.write(content)
            private_file.flush()
            os.fsync(private_file.fileno())
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_fd)
        os.close(directory_fd)


def read_private_text(path: Path) -> str | None:
    """Read a private POSIX text file through a pinned parent descriptor."""
    if os.name == "nt":
        return _read_windows_private_text(path)
    directory_fd = _open_posix_parent(path)
    try:
        try:
            descriptor = _open_regular(directory_fd, path.name, os.O_RDONLY, path)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, encoding="utf-8") as private_file:
            return private_file.read()
    finally:
        os.close(directory_fd)


def delete_private_file(path: Path) -> None:
    """Delete a private POSIX file without following a redirected parent."""
    if os.name == "nt":
        _delete_windows_private_file(path)
        return
    directory_fd = _open_posix_parent(path)
    try:
        with suppress(FileNotFoundError):
            os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _open_posix_parent(path: Path) -> int:
    if os.name != "posix":
        raise PrivateFileUnsupportedError(path)
    directory_fd = open_private_parent(path)
    if directory_fd is None:
        raise PrivateFileUnsupportedError(path)
    return directory_fd


def _open_regular(
    directory_fd: int,
    name: str,
    flags: int,
    path: Path,
) -> int:
    descriptor = os.open(
        name,
        flags | os.O_NOFOLLOW,
        _PRIVATE_FILE_MODE,
        dir_fd=directory_fd,
    )
    observed = os.fstat(descriptor)
    if not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.getuid():
        os.close(descriptor)
        raise UnsafeDatabasePathError(path, "private file owner or type is unsafe")
    os.fchmod(descriptor, _PRIVATE_FILE_MODE)
    return descriptor


def _write_windows_private_text(path: Path, content: str) -> None:
    """Replace a DACL-protected Windows file from a protected sibling temp."""
    _ = open_private_parent(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        prepare_private_database_file(None, temporary)
        with temporary.open("w", encoding="utf-8") as private_file:
            _ = private_file.write(content)
            private_file.flush()
            os.fsync(private_file.fileno())
        _ = temporary.replace(path)
        prepare_private_database_file(None, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _read_windows_private_text(path: Path) -> str | None:
    _ = open_private_parent(path)
    if not path.exists():
        return None
    prepare_private_database_file(None, path)
    return path.read_text(encoding="utf-8")


def _delete_windows_private_file(path: Path) -> None:
    _ = open_private_parent(path)
    if not path.exists():
        return
    prepare_private_database_file(None, path)
    path.unlink()
