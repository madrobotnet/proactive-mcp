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
    """Atomically replace a private text file through a pinned parent."""
    if os.name == "nt":
        _write_windows_private_text(path, content)
        return
    directory_fd = _open_posix_parent(path)
    temporary_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
    temporary_fd: int | None = None
    destination_fd: int | None = None
    try:
        destination_fd = _open_optional(directory_fd, path.name, path)
        temporary_fd = _open_regular(
            directory_fd,
            temporary_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            path,
        )
        with os.fdopen(os.dup(temporary_fd), "w", encoding="utf-8") as private_file:
            _ = private_file.write(content)
            private_file.flush()
            os.fsync(private_file.fileno())
        _verify_open_name(directory_fd, temporary_name, temporary_fd, path)
        if destination_fd is not None:
            _verify_open_name(directory_fd, path.name, destination_fd, path)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        _verify_open_name(directory_fd, path.name, temporary_fd, path)
        os.fsync(directory_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if destination_fd is not None:
            os.close(destination_fd)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_fd)
        os.close(directory_fd)


def read_private_text(path: Path) -> str | None:
    """Read a private text file through a pinned parent descriptor."""
    if os.name == "nt":
        return _read_windows_private_text(path)
    directory_fd = _open_posix_parent(path)
    descriptor: int | None = None
    try:
        try:
            descriptor = _open_regular(directory_fd, path.name, os.O_RDONLY, path)
        except FileNotFoundError:
            return None
        with os.fdopen(os.dup(descriptor), encoding="utf-8") as private_file:
            content = private_file.read()
        _verify_open_name(directory_fd, path.name, descriptor, path)
        return content
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def delete_private_file(path: Path) -> None:
    """Delete one verified private file without following a redirected parent."""
    if os.name == "nt":
        _delete_windows_private_file(path)
        return
    directory_fd = _open_posix_parent(path)
    descriptor: int | None = None
    try:
        descriptor = _open_optional(directory_fd, path.name, path)
        if descriptor is None:
            return
        _verify_open_name(directory_fd, path.name, descriptor, path)
        os.unlink(path.name, dir_fd=directory_fd)
        if os.fstat(descriptor).st_nlink != 0:
            raise UnsafeDatabasePathError(path, "private file changed while deleted")
        os.fsync(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def _open_posix_parent(path: Path) -> int:
    if os.name != "posix":
        raise PrivateFileUnsupportedError(path)
    directory_fd = open_private_parent(path)
    if directory_fd is None:
        raise PrivateFileUnsupportedError(path)
    return directory_fd


def _open_optional(directory_fd: int, name: str, path: Path) -> int | None:
    try:
        return _open_regular(directory_fd, name, os.O_RDONLY, path)
    except FileNotFoundError:
        return None


def _open_regular(directory_fd: int, name: str, flags: int, path: Path) -> int:
    descriptor = os.open(
        name,
        flags | os.O_NOFOLLOW,
        _PRIVATE_FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        _verify_identity(os.fstat(descriptor), path)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != _PRIVATE_FILE_MODE:
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        _verify_open_name(directory_fd, name, descriptor, path)
    except (OSError, UnsafeDatabasePathError):
        os.close(descriptor)
        raise
    return descriptor


def _verify_open_name(
    directory_fd: int,
    name: str,
    descriptor: int,
    path: Path,
) -> None:
    observed = os.fstat(descriptor)
    _verify_identity(observed, path)
    current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
        raise UnsafeDatabasePathError(path, "private file identity changed")
    _verify_identity(current, path)


def _verify_identity(observed: os.stat_result, path: Path) -> None:
    safe = (
        stat.S_ISREG(observed.st_mode)
        and observed.st_uid == os.getuid()
        and observed.st_nlink == 1
    )
    if not safe:
        raise UnsafeDatabasePathError(path, "private file identity is unsafe")


def _write_windows_private_text(path: Path, content: str) -> None:
    """Replace a DACL-protected Windows file from a protected sibling temp."""
    _ = open_private_parent(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        _ = prepare_private_database_file(None, temporary)
        with temporary.open("w", encoding="utf-8") as private_file:
            _ = private_file.write(content)
            private_file.flush()
            os.fsync(private_file.fileno())
        _ = temporary.replace(path)
        _ = prepare_private_database_file(None, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _read_windows_private_text(path: Path) -> str | None:
    _ = open_private_parent(path)
    if not path.exists():
        return None
    _ = prepare_private_database_file(None, path)
    return path.read_text(encoding="utf-8")


def _delete_windows_private_file(path: Path) -> None:
    _ = open_private_parent(path)
    if not path.exists():
        return
    _ = prepare_private_database_file(None, path)
    path.unlink()
