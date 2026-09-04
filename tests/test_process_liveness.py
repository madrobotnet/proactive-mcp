"""Hermetic POSIX and injected Win32 process-liveness mapping."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from ctypes import wintypes

import pytest

from proactive_mcp.cli import process_liveness as process_liveness_module
from proactive_mcp.cli.process_liveness import (
    ERROR_ACCESS_DENIED,
    ERROR_INVALID_PARAMETER,
    PROCESS_QUERY_LIMITED_INFORMATION,
    STILL_ACTIVE,
    process_is_alive,
    windows_pid_is_alive,
)

_HANDLE = 7
_IMPOSSIBLE_PID = 2_147_483_647
_PID = 4242


class _FakeWindowsPidApi:
    """Mutable recording fake for Win32 process-query calls."""

    __slots__: tuple[str, ...] = (
        "closes",
        "exit_code",
        "get_exit_ok",
        "handle",
        "last_error",
        "opens",
    )

    closes: list[int]
    exit_code: int
    get_exit_ok: bool
    handle: int
    last_error: int
    opens: list[tuple[int, int, int]]

    def __init__(
        self,
        *,
        handle: int = 0,
        last_error: int = 0,
        exit_result: tuple[bool, int] = (True, 0),
    ) -> None:
        self.handle = handle
        self.last_error = last_error
        self.get_exit_ok, self.exit_code = exit_result
        self.opens = []
        self.closes = []

    def open_process(
        self, desired_access: int, inherit_handle: int, process_id: int
    ) -> int:
        self.opens.append((desired_access, inherit_handle, process_id))
        return self.handle

    def get_exit_code_process(self, handle: int, exit_code: wintypes.DWORD) -> int:
        del handle
        exit_code.value = self.exit_code
        return int(self.get_exit_ok)

    def close_handle(self, handle: int) -> int:
        self.closes.append(handle)
        return 1

    def get_last_error(self) -> int:
        return self.last_error


def _forbid_kill(_pid: int, _signal: int) -> NoReturn:
    raise AssertionError


def test_windows_pid_access_denied_is_alive() -> None:
    # Given: OpenProcess fails with ERROR_ACCESS_DENIED.
    api = _FakeWindowsPidApi(handle=0, last_error=ERROR_ACCESS_DENIED)

    # When: liveness is queried through the Win32 mapping.
    alive = windows_pid_is_alive(_PID, api=api)

    # Then: the process is treated as alive and no handle is closed.
    assert alive is True
    assert api.closes == []


@pytest.mark.parametrize("last_error", [ERROR_INVALID_PARAMETER, 6, 0])
def test_windows_pid_invalid_or_dead_is_dead(last_error: int) -> None:
    # Given: OpenProcess fails with a non-access-denied Win32 error.
    api = _FakeWindowsPidApi(handle=0, last_error=last_error)

    # When: liveness is queried through the Win32 mapping.
    alive = windows_pid_is_alive(_PID, api=api)

    # Then: the process is dead and no handle is closed.
    assert alive is False
    assert api.closes == []


def test_windows_pid_still_active_is_alive() -> None:
    # Given: OpenProcess succeeds and the exit code is STILL_ACTIVE.
    api = _FakeWindowsPidApi(handle=_HANDLE, exit_result=(True, STILL_ACTIVE))

    # When: liveness is queried through the Win32 mapping.
    alive = windows_pid_is_alive(_PID, api=api)

    # Then: the process is alive and the handle is closed once.
    assert alive is True
    assert api.closes == [_HANDLE]


def test_windows_pid_nonzero_exit_is_dead() -> None:
    # Given: OpenProcess succeeds and the process has already exited.
    api = _FakeWindowsPidApi(handle=_HANDLE, exit_result=(True, 0))

    # When: liveness is queried through the Win32 mapping.
    alive = windows_pid_is_alive(_PID, api=api)

    # Then: the process is dead and the handle is closed once.
    assert alive is False
    assert api.closes == [_HANDLE]


def test_windows_pid_get_exit_code_failure_closes_once() -> None:
    # Given: OpenProcess succeeds but GetExitCodeProcess fails.
    api = _FakeWindowsPidApi(handle=_HANDLE, exit_result=(False, 0))

    # When: liveness is queried through the Win32 mapping.
    alive = windows_pid_is_alive(_PID, api=api)

    # Then: the opened handle still implies alive, and it is closed once.
    assert alive is True
    assert api.closes == [_HANDLE]


def test_windows_open_process_uses_query_limited_information() -> None:
    # Given: a recording Win32 API that never terminates.
    api = _FakeWindowsPidApi(handle=_HANDLE, exit_result=(True, STILL_ACTIVE))

    # When: liveness opens the process.
    _ = windows_pid_is_alive(_PID, api=api)

    # Then: OpenProcess uses PROCESS_QUERY_LIMITED_INFORMATION without inherit.
    assert api.opens == [(PROCESS_QUERY_LIMITED_INFORMATION, 0, _PID)]


def test_process_is_alive_current_pid() -> None:
    # Given: this process.
    # When: POSIX liveness is queried.
    # Then: the current PID is alive.
    assert process_is_alive(os.getpid()) is True


def test_process_is_alive_impossible_pid() -> None:
    # Given: a PID that cannot exist on this host.
    # When: POSIX liveness is queried.
    # Then: the PID is dead and no exception escapes.
    assert process_is_alive(_IMPOSSIBLE_PID) is False


def test_process_is_alive_uses_injected_windows_api_on_any_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an injected Win32 API on POSIX and a forbidden os.kill.
    pid = os.getpid()
    api = _FakeWindowsPidApi(handle=_HANDLE, exit_result=(True, STILL_ACTIVE))
    monkeypatch.setattr(os, "kill", _forbid_kill)

    # When: process_is_alive is routed through the injectable branch.
    alive = process_is_alive(pid, windows_api=api)

    # Then: os.kill is skipped and OpenProcess receives the queried PID.
    assert alive is True
    assert api.opens[-1][2] == pid


def test_process_is_alive_win32_does_not_call_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a win32 platform, lazy API loader, and forbidden os.kill.
    api = _FakeWindowsPidApi(handle=_HANDLE, exit_result=(True, STILL_ACTIVE))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        process_liveness_module,
        "_load_windows_pid_api",
        lambda: api,
    )
    monkeypatch.setattr(os, "kill", _forbid_kill)

    # When: process_is_alive is asked about a foreign PID.
    alive = process_is_alive(_PID)

    # Then: the Win32 path never calls os.kill.
    assert alive is True
    assert api.opens == [(PROCESS_QUERY_LIMITED_INFORMATION, 0, _PID)]
