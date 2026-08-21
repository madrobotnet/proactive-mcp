"""Cross-platform private filesystem access for the SQLite store."""

from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, TypeGuard

from .storage_errors import UnsafeDatabasePathError

if TYPE_CHECKING:
    from collections.abc import Generator
    from contextlib import AbstractContextManager
    from pathlib import Path
    from types import ModuleType


class _PosixBackend(Protocol):
    """POSIX descriptor-pinned backend contract."""

    def open_private_parent(self, path: Path) -> int: ...

    def prepare_private_database_file(self, directory_fd: int, path: Path) -> None: ...

    def private_initialization_lock(
        self,
        directory_fd: int,
        path: Path,
    ) -> AbstractContextManager[None]: ...

    def enforce_private_sidecars(self, directory_fd: int, path: Path) -> None: ...


class _WindowsBackend(Protocol):
    """Windows protected-DACL backend contract."""

    def prepare_private_parent(self, path: Path) -> None: ...

    def prepare_private_database_file(self, path: Path) -> None: ...

    def private_initialization_lock(
        self,
        path: Path,
    ) -> AbstractContextManager[None]: ...

    def enforce_private_sidecars(self, path: Path) -> None: ...


def _uses_windows_backend() -> bool:
    return os.name == "nt"


def _is_posix_backend(module: ModuleType) -> TypeGuard[_PosixBackend]:
    return all(
        hasattr(module, name)
        for name in (
            "open_private_parent",
            "prepare_private_database_file",
            "private_initialization_lock",
            "enforce_private_sidecars",
        )
    )


def _is_windows_backend(module: ModuleType) -> TypeGuard[_WindowsBackend]:
    return all(
        hasattr(module, name)
        for name in (
            "prepare_private_parent",
            "prepare_private_database_file",
            "private_initialization_lock",
            "enforce_private_sidecars",
        )
    )


def _posix_backend(path: Path) -> _PosixBackend:
    module = importlib.import_module(f"{__package__}._posix_private_path")
    if not _is_posix_backend(module):
        raise UnsafeDatabasePathError(path, "POSIX storage backend is invalid")
    return module


def _windows_backend(path: Path) -> _WindowsBackend:
    module = importlib.import_module(f"{__package__}.windows_path")
    if not _is_windows_backend(module):
        raise UnsafeDatabasePathError(path, "Windows storage backend is invalid")
    return module


def open_private_parent(path: Path) -> int | None:
    """Prepare a private parent, pinning a descriptor on POSIX platforms."""
    if ".." in path.parts:
        raise UnsafeDatabasePathError(path, "parent traversal is not allowed")
    if sys.platform not in ("linux", "darwin", "win32"):
        raise UnsafeDatabasePathError(path, "unsupported storage platform")
    if _uses_windows_backend():
        _windows_backend(path).prepare_private_parent(path)
        return None
    return _posix_backend(path).open_private_parent(path)


def prepare_private_database_file(directory_fd: int | None, path: Path) -> None:
    """Create or open the database file and reject unsafe sidecars."""
    if _uses_windows_backend():
        _windows_backend(path).prepare_private_database_file(path)
        return
    if directory_fd is None:
        raise UnsafeDatabasePathError(path, "POSIX directory handle is unavailable")
    _posix_backend(path).prepare_private_database_file(directory_fd, path)


@contextmanager
def private_initialization_lock(
    directory_fd: int | None,
    path: Path,
) -> Generator[None]:
    """Serialize WAL setup and migrations across fresh Store processes."""
    if _uses_windows_backend():
        with _windows_backend(path).private_initialization_lock(path):
            yield
        return
    if directory_fd is None:
        raise UnsafeDatabasePathError(path, "POSIX directory handle is unavailable")
    with _posix_backend(path).private_initialization_lock(directory_fd, path):
        yield


def enforce_private_sidecars(directory_fd: int | None, path: Path) -> None:
    """Enforce private modes on the database and existing WAL sidecars."""
    if _uses_windows_backend():
        _windows_backend(path).enforce_private_sidecars(path)
        return
    if directory_fd is None:
        raise UnsafeDatabasePathError(path, "POSIX directory handle is unavailable")
    _posix_backend(path).enforce_private_sidecars(directory_fd, path)


def sqlite_connection_target(directory_fd: int | None, path: Path) -> str:
    """Return the Linux descriptor-pinned target or the real absolute path."""
    if directory_fd is not None and sys.platform == "linux":
        return f"/proc/self/fd/{directory_fd:d}/{path.name}"
    return str(path)


__all__ = [
    "UnsafeDatabasePathError",
    "enforce_private_sidecars",
    "open_private_parent",
    "prepare_private_database_file",
    "private_initialization_lock",
    "sqlite_connection_target",
]
