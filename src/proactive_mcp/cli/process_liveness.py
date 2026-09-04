"""Typed process liveness checks with a nonterminating Win32 probe."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import TYPE_CHECKING, Final, Protocol, cast, final

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ERROR_ACCESS_DENIED",
    "ERROR_INVALID_PARAMETER",
    "PROCESS_QUERY_LIMITED_INFORMATION",
    "STILL_ACTIVE",
    "WindowsPidApi",
    "process_is_alive",
    "windows_pid_is_alive",
]

ERROR_ACCESS_DENIED: Final[int] = 5
ERROR_INVALID_PARAMETER: Final[int] = 87
PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000
STILL_ACTIVE: Final[int] = 259


class WindowsPidApi(Protocol):
    """Injectable kernel32 surface; never terminates."""

    def open_process(
        self, desired_access: int, inherit_handle: int, process_id: int
    ) -> int:
        """Return a process handle, or 0 on failure."""
        ...

    def get_exit_code_process(self, handle: int, exit_code: wintypes.DWORD) -> int:
        """Mutate ``exit_code.value``; return a Win32 BOOL."""
        ...

    def close_handle(self, handle: int) -> int:
        """Release ``handle``; return a Win32 BOOL."""
        ...

    def get_last_error(self) -> int:
        """Return ``GetLastError`` for the previous call."""
        ...


def process_is_alive(
    pid: int,
    *,
    windows_api: WindowsPidApi | None = None,
) -> bool:
    """Return whether *pid* is alive without terminating it."""
    if windows_api is not None:
        return windows_pid_is_alive(pid, api=windows_api)
    if sys.platform == "win32":
        return windows_pid_is_alive(pid, api=_load_windows_pid_api())
    return _posix_pid_is_alive(pid)


def windows_pid_is_alive(pid: int, *, api: WindowsPidApi) -> bool:
    """Return whether *pid* is alive using a nonterminating Win32 query."""
    handle = api.open_process(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if handle == 0:
        return api.get_last_error() == ERROR_ACCESS_DENIED
    exit_code = wintypes.DWORD(0)
    try:
        if not api.get_exit_code_process(handle, exit_code):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        _ = api.close_handle(handle)


def _posix_pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@final
class _Kernel32PidApi:
    """Lazy kernel32 adapter that never terminates a process."""

    _open_process: Callable[[int, int, int], int | None]
    _get_exit_code_process: Callable[..., int | None]
    _close_handle: Callable[[int], int | None]

    def __init__(self) -> None:
        """Bind typed kernel32 process-query functions."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process_type = ctypes.WINFUNCTYPE(
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            use_last_error=True,
        )
        open_process = cast(
            "Callable[[int, int, int], int | None]",
            open_process_type(("OpenProcess", kernel32)),
        )
        get_exit_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
            use_last_error=True,
        )
        get_exit = cast(
            "Callable[..., int | None]",
            get_exit_type(("GetExitCodeProcess", kernel32)),
        )
        close_handle_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            use_last_error=True,
        )
        close_handle = cast(
            "Callable[[int], int | None]",
            close_handle_type(("CloseHandle", kernel32)),
        )
        self._open_process = open_process
        self._get_exit_code_process = get_exit
        self._close_handle = close_handle

    def open_process(
        self, desired_access: int, inherit_handle: int, process_id: int
    ) -> int:
        """Return a process handle, or 0 on failure."""
        handle = self._open_process(desired_access, inherit_handle, process_id)
        if handle is None:
            return 0
        return int(handle)

    def get_exit_code_process(self, handle: int, exit_code: wintypes.DWORD) -> int:
        """Mutate ``exit_code.value``; return a Win32 BOOL."""
        result = self._get_exit_code_process(handle, ctypes.byref(exit_code))
        return 0 if result is None else int(result)

    def close_handle(self, handle: int) -> int:
        """Release ``handle``; return a Win32 BOOL."""
        result = self._close_handle(handle)
        return 0 if result is None else int(result)

    def get_last_error(self) -> int:
        """Return ``GetLastError`` for the previous call."""
        return int(ctypes.get_last_error())


def _load_windows_pid_api() -> WindowsPidApi:
    return _Kernel32PidApi()
