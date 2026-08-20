"""Descriptor-pinned private filesystem access for the SQLite store."""

from __future__ import annotations

import fcntl
import os
import stat
import sys
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_PRIVATE_DIRECTORY_MODE: Final[int] = 0o700
_PRIVATE_FILE_MODE: Final[int] = 0o600


@dataclass(frozen=True, slots=True)
class UnsafeDatabasePathError(Exception):
    """Raised when the database path could expose or redirect private data."""

    path: Path
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a non-sensitive message."""
        Exception.__init__(self, f"unsafe database path: {self.reason}")


def open_private_parent(path: Path) -> int:
    """Open and pin a Linux database directory without following symlinks."""
    if sys.platform != "linux":
        raise UnsafeDatabasePathError(path, "secure database storage requires Linux")
    if ".." in path.parts:
        raise UnsafeDatabasePathError(path, "parent traversal is not allowed")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current_fd = os.open("/", directory_flags)
    try:
        parent_parts = path.parent.parts[1:]
        for index, part in enumerate(parent_parts):
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except FileNotFoundError:
                with suppress(FileExistsError):
                    os.mkdir(part, _PRIVATE_DIRECTORY_MODE, dir_fd=current_fd)
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as error:
                raise UnsafeDatabasePathError(
                    path,
                    "parent directory cannot be opened safely",
                ) from error
            os.close(current_fd)
            current_fd = next_fd
            _verify_private_directory(
                current_fd,
                path,
                is_database_parent=index == len(parent_parts) - 1,
            )
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def prepare_private_database_file(directory_fd: int, path: Path) -> None:
    """Create or open the database file and reject unsafe sidecars."""
    descriptor = _open_private_file(
        directory_fd,
        path.name,
        os.O_CREAT | os.O_RDWR,
        path,
    )
    os.close(descriptor)
    enforce_private_sidecars(directory_fd, path)


@contextmanager
def private_initialization_lock(
    directory_fd: int,
    path: Path,
) -> Generator[None]:
    """Serialize WAL setup and migrations across fresh Store processes."""
    lock_fd = _open_private_file(
        directory_fd,
        f".{path.name}.init.lock",
        os.O_CREAT | os.O_RDWR,
        path,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def enforce_private_sidecars(directory_fd: int, path: Path) -> None:
    """Enforce private modes on the database and existing WAL sidecars."""
    database_fd = _open_private_file(directory_fd, path.name, os.O_RDONLY, path)
    os.close(database_fd)
    for suffix in ("-wal", "-shm"):
        try:
            sidecar_fd = os.open(
                f"{path.name}{suffix}",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            continue
        except OSError as error:
            raise UnsafeDatabasePathError(
                path,
                "database sidecar cannot be opened safely",
            ) from error
        try:
            if not stat.S_ISREG(os.fstat(sidecar_fd).st_mode):
                raise UnsafeDatabasePathError(
                    path,
                    "database sidecar is not a regular file",
                )
            os.fchmod(sidecar_fd, _PRIVATE_FILE_MODE)
        finally:
            os.close(sidecar_fd)


def _verify_private_directory(
    directory_fd: int,
    path: Path,
    *,
    is_database_parent: bool,
) -> None:
    observed = os.fstat(directory_fd)
    if not stat.S_ISDIR(observed.st_mode):
        raise UnsafeDatabasePathError(path, "path component is not a directory")
    current_user = os.getuid()
    if observed.st_uid not in (0, current_user):
        raise UnsafeDatabasePathError(path, "path component has an untrusted owner")
    if is_database_parent:
        if observed.st_uid != current_user:
            raise UnsafeDatabasePathError(path, "database directory is not user-owned")
        os.fchmod(directory_fd, _PRIVATE_DIRECTORY_MODE)
        return
    writable_by_others = observed.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if writable_by_others and not observed.st_mode & stat.S_ISVTX:
        raise UnsafeDatabasePathError(path, "ancestor directory is writable by others")


def _open_private_file(
    directory_fd: int,
    name: str,
    flags: int,
    path: Path,
) -> int:
    try:
        descriptor = os.open(
            name,
            flags | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
            dir_fd=directory_fd,
        )
    except OSError as error:
        raise UnsafeDatabasePathError(
            path,
            "private file cannot be opened safely",
        ) from error
    observed = os.fstat(descriptor)
    if not stat.S_ISREG(observed.st_mode):
        os.close(descriptor)
        raise UnsafeDatabasePathError(path, "private path is not a regular file")
    os.fchmod(descriptor, _PRIVATE_FILE_MODE)
    return descriptor


__all__ = [
    "UnsafeDatabasePathError",
    "enforce_private_sidecars",
    "open_private_parent",
    "prepare_private_database_file",
    "private_initialization_lock",
]
