"""Windows private filesystem operations backed by Win32 security APIs."""

from __future__ import annotations

import ctypes
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, NoReturn, cast

from ._windows_bindings import (
    ERROR_FILE_NOT_FOUND,
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_NORMAL,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_DELETE,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_READ,
    GENERIC_WRITE,
    INVALID_HANDLE_VALUE,
    LOCKFILE_EXCLUSIVE_LOCK,
    NO_INHERITANCE,
    READ_CONTROL,
    SUB_CONTAINERS_AND_OBJECTS_INHERIT,
    WRITE_DAC,
    ByHandleFileInformation,
    Overlapped,
    kernel32,
)
from ._windows_dacl import require_current_user_owner
from ._windows_dacl import set_private_dacl as _set_private_dacl
from .storage_errors import UnsafeDatabasePathError

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_WINDOWS_ONLY_MESSAGE: Final[str] = (
    "proactive_mcp.store.windows_path is available only on Windows"
)

if sys.platform != "win32":
    raise ImportError(_WINDOWS_ONLY_MESSAGE)

_OPEN_ALWAYS: Final[int] = 4
_OPEN_EXISTING: Final[int] = 3


def prepare_private_parent(path: Path) -> None:
    """Create the database parent and restrict it to the current user."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise UnsafeDatabasePathError(
            path,
            "database directory cannot be created safely",
        ) from error
    _secure_directory(path.parent, path)


def prepare_private_database_file(path: Path) -> None:
    """Create or open the database and restrict its DACL to the current user."""
    _secure_regular_file(path, path, _OPEN_ALWAYS)
    enforce_private_sidecars(path)


@contextmanager
def private_initialization_lock(path: Path) -> Generator[None, None, None]:
    """Serialize WAL setup and migrations with a Windows byte-range lock."""
    lock_path = path.with_name(f".{path.name}.init.lock")
    handle = _open_regular_file(lock_path, path, _OPEN_ALWAYS)
    try:
        set_private_dacl(handle, path, inheritance=NO_INHERITANCE)
        _lock_file(handle, path)
        try:
            yield
        finally:
            _unlock_file(handle, path)
    finally:
        _close_handle(handle, path)


@contextmanager
def private_database_guard(path: Path) -> Generator[None, None, None]:
    """Pin the verified parent and database identities for the Store lifetime."""
    parent_handle = _open_path(
        path.parent,
        path,
        _OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
    )
    database_handle = INVALID_HANDLE_VALUE
    try:
        _verify_directory(parent_handle, path)
        require_current_user_owner(parent_handle, path)
        database_handle = _open_regular_file(
            path,
            path,
            _OPEN_EXISTING,
            share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
        )
        yield
    finally:
        _close_handle(database_handle, path)
        _close_handle(parent_handle, path)


def enforce_private_sidecars(path: Path) -> None:
    """Secure the database and its existing SQLite WAL and SHM sidecars."""
    _secure_regular_file(path, path, _OPEN_EXISTING)
    for suffix in ("-wal", "-shm"):
        sidecar_path = path.with_name(f"{path.name}{suffix}")
        handle = _open_existing_regular_file(sidecar_path, path)
        if handle is None:
            continue
        try:
            set_private_dacl(handle, path, inheritance=NO_INHERITANCE)
        finally:
            _close_handle(handle, path)


def set_private_dacl(handle: int, path: Path, *, inheritance: int) -> None:
    """Apply a protected current-user DACL to an opened filesystem object."""
    _set_private_dacl(handle, path, inheritance=inheritance)


def _secure_directory(directory: Path, database_path: Path) -> None:
    handle = _open_path(
        directory,
        database_path,
        _OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
    )
    try:
        _verify_directory(handle, database_path)
        require_current_user_owner(handle, database_path)
        set_private_dacl(
            handle,
            database_path,
            inheritance=SUB_CONTAINERS_AND_OBJECTS_INHERIT,
        )
    finally:
        _close_handle(handle, database_path)


def _secure_regular_file(
    file_path: Path,
    database_path: Path,
    creation_disposition: int,
) -> None:
    handle = _open_regular_file(file_path, database_path, creation_disposition)
    try:
        set_private_dacl(handle, database_path, inheritance=NO_INHERITANCE)
    finally:
        _close_handle(handle, database_path)


def _open_regular_file(
    file_path: Path,
    database_path: Path,
    creation_disposition: int,
    *,
    share_mode: int = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
) -> int:
    handle = _open_path(
        file_path,
        database_path,
        creation_disposition,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL,
        share_mode=share_mode,
    )
    try:
        _verify_regular_file(handle, database_path)
        require_current_user_owner(handle, database_path)
    except UnsafeDatabasePathError:
        _close_handle(handle, database_path)
        raise
    return handle


def _open_existing_regular_file(file_path: Path, database_path: Path) -> int | None:
    try:
        return _open_regular_file(file_path, database_path, _OPEN_EXISTING)
    except UnsafeDatabasePathError as error:
        if _win32_error_code(error) == ERROR_FILE_NOT_FOUND:
            return None
        raise


def _open_path(
    file_path: Path,
    database_path: Path,
    creation_disposition: int,
    flags_and_attributes: int,
    *,
    share_mode: int = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
) -> int:
    handle = kernel32.create_file(
        str(file_path),
        GENERIC_READ | GENERIC_WRITE | READ_CONTROL | WRITE_DAC,
        share_mode,
        None,
        creation_disposition,
        flags_and_attributes,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        _raise_last_error(database_path, "private path cannot be opened safely")
    return handle


def _verify_directory(handle: int, database_path: Path) -> None:
    information = _file_information(handle, database_path)
    if not information.file_attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise UnsafeDatabasePathError(
            database_path, "database parent is not a directory"
        )
    if information.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafeDatabasePathError(
            database_path,
            "database directory cannot be a reparse point",
        )


def _verify_regular_file(handle: int, database_path: Path) -> None:
    information = _file_information(handle, database_path)
    if information.file_attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise UnsafeDatabasePathError(
            database_path, "private path is not a regular file"
        )
    if information.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafeDatabasePathError(
            database_path,
            "private path cannot be a reparse point",
        )
    if cast("int", information.number_of_links) != 1:
        raise UnsafeDatabasePathError(
            database_path,
            "private path must have exactly one hard link",
        )


def _file_information(handle: int, database_path: Path) -> ByHandleFileInformation:
    information = ByHandleFileInformation()
    if not kernel32.file_information(handle, ctypes.byref(information)):
        _raise_last_error(database_path, "private path metadata cannot be read")
    return information


def _lock_file(handle: int, path: Path) -> None:
    overlapped = Overlapped()
    if not kernel32.lock_file(
        handle,
        LOCKFILE_EXCLUSIVE_LOCK,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        _raise_last_error(path, "initialization lock cannot be acquired")


def _unlock_file(handle: int, path: Path) -> None:
    overlapped = Overlapped()
    if not kernel32.unlock_file(handle, 0, 1, 0, ctypes.byref(overlapped)):
        _raise_last_error(path, "initialization lock cannot be released")


def _close_handle(handle: int, path: Path) -> None:
    if handle == INVALID_HANDLE_VALUE:
        return
    if not kernel32.close_handle(handle):
        _raise_last_error(path, "Windows handle cannot be closed")


def _raise_last_error(path: Path, reason: str) -> NoReturn:
    _raise_error_code(path, ctypes.get_last_error(), reason)


def _raise_error_code(path: Path, error_code: int, reason: str) -> NoReturn:
    error = OSError(error_code, "Win32 operation failed")
    raise UnsafeDatabasePathError(path, reason) from error


def _win32_error_code(error: UnsafeDatabasePathError) -> int | None:
    cause = error.__cause__
    if isinstance(cause, OSError):
        return cause.errno
    return None


__all__ = [
    "enforce_private_sidecars",
    "prepare_private_database_file",
    "prepare_private_parent",
    "private_database_guard",
    "private_initialization_lock",
    "set_private_dacl",
]
