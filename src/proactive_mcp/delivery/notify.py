"""OS notification fallback via argv-only subprocess calls."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

    from proactive_mcp.delivery.payload import NotificationPayload

__all__ = [
    "DEFAULT_NOTIFICATION_TIMEOUT",
    "MACOS_NOTIFICATION_SCRIPT",
    "WINDOWS_TOAST_SCRIPT",
    "NotificationError",
    "NotificationErrorCode",
    "NotificationHost",
    "NotificationPlatform",
    "NotificationRunner",
    "SubprocessNotificationRunner",
    "notification_available",
    "parse_notification_platform",
    "send_os_notification",
    "trusted_notifier_path",
]

NotificationErrorCode: TypeAlias = Literal[
    "timeout",
    "unavailable",
    "failed",
    "unsupported_platform",
]
NotificationPlatform: TypeAlias = Literal["linux", "darwin", "win32"]


class _GetSystemDirectory(Protocol):
    """Typed view of the dynamically loaded Win32 directory function."""

    argtypes: list[object]
    restype: object

    def __call__(self, buffer: object, size: int, /) -> int:
        """Write the system directory and return its character count."""
        ...


DEFAULT_NOTIFICATION_TIMEOUT: Final = timedelta(seconds=5)
MACOS_NOTIFICATION_SCRIPT: Final = (
    Path(__file__).resolve().parent / "macos_notification.applescript"
)
WINDOWS_TOAST_SCRIPT: Final = Path(__file__).resolve().parent / "windows_toast.ps1"
_UNAVAILABLE: Final[NotificationErrorCode] = "unavailable"
_TIMEOUT: Final[NotificationErrorCode] = "timeout"
_FAILED: Final[NotificationErrorCode] = "failed"
_UNSUPPORTED: Final[NotificationErrorCode] = "unsupported_platform"
_PLATFORMS: Final[dict[str, NotificationPlatform]] = {
    "linux": "linux",
    "darwin": "darwin",
    "win32": "win32",
}
_LINUX_NOTIFY_SEND: Final = "/usr/bin/notify-send"
_MACOS_OSASCRIPT: Final = "/usr/bin/osascript"


@dataclass(frozen=True, slots=True)
class NotificationError(Exception):
    """A status-safe OS notification failure."""

    error_code: NotificationErrorCode

    def __post_init__(self) -> None:
        """Initialize the base exception with the redacted code only."""
        Exception.__init__(self, self.error_code)


class NotificationRunner(Protocol):
    """Execute one argv vector without a shell."""

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        """Run argv or raise NotificationError."""


@dataclass(frozen=True, slots=True)
class NotificationHost:
    """Platform and process runner for one notification attempt."""

    platform: NotificationPlatform
    runner: NotificationRunner


class SubprocessNotificationRunner:
    """Run a notification command as an argv vector with a bounded timeout."""

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        """Invoke argv with shell disabled and stdio discarded."""
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout.total_seconds(),
                cwd=str(Path(argv[0]).parent),
                env=_sanitized_environment(),
            )
        except FileNotFoundError:
            raise NotificationError(_UNAVAILABLE) from None
        except subprocess.TimeoutExpired:
            raise NotificationError(_TIMEOUT) from None
        except OSError:
            raise NotificationError(_FAILED) from None
        if completed.returncode != 0:
            raise NotificationError(_FAILED)


def parse_notification_platform(raw: str) -> NotificationPlatform:
    """Parse a sys.platform value into a supported notification platform."""
    try:
        return _PLATFORMS[raw]
    except KeyError:
        raise NotificationError(_UNSUPPORTED) from None


def notification_available() -> bool:
    """Return whether this host exposes its trusted notifier executable."""
    try:
        platform = parse_notification_platform(sys.platform)
        notifier = Path(trusted_notifier_path(platform))
    except (NotificationError, OSError):
        return False
    return notifier.is_file()


def send_os_notification(
    payload: NotificationPayload,
    host: NotificationHost,
) -> None:
    """Deliver one isolated payload through the host OS notifier."""
    argv = _notification_argv(payload, host.platform)
    host.runner.run(argv, DEFAULT_NOTIFICATION_TIMEOUT)


def _notification_argv(
    payload: NotificationPayload,
    platform: NotificationPlatform,
) -> tuple[str, ...]:
    title = payload.title
    body = payload.situation_type
    commands: dict[NotificationPlatform, tuple[str, ...]] = {
        "linux": (trusted_notifier_path("linux"), "--", title, body),
        "darwin": (
            trusted_notifier_path("darwin"),
            str(MACOS_NOTIFICATION_SCRIPT),
            title,
            body,
        ),
        "win32": (
            trusted_notifier_path("win32"),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(WINDOWS_TOAST_SCRIPT),
            title,
            body,
        ),
    }
    return commands[platform]


def trusted_notifier_path(platform: NotificationPlatform) -> str:
    """Resolve each notifier from an OS-owned absolute location, never PATH."""
    if platform == "linux":
        return _LINUX_NOTIFY_SEND
    if platform == "darwin":
        return _MACOS_OSASCRIPT
    powershell = (
        PureWindowsPath(_windows_system_directory())
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(powershell)


def _windows_system_directory() -> str:
    if sys.platform != "win32":
        return r"C:\Windows\System32"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = cast(
        "_GetSystemDirectory",
        cast("object", kernel32.GetSystemDirectoryW),
    )
    get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32_768)
    length = get_system_directory(buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise NotificationError(_UNAVAILABLE)
    return cast("str", buffer.value)


def _sanitized_environment() -> dict[str, str]:
    """Retain session variables while removing executable injection controls."""
    blocked = {
        "DYLD_INSERT_LIBRARIES",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PSModulePath",
        "PYTHONHOME",
        "PYTHONPATH",
    }
    environment = {
        key: value for key, value in os.environ.items() if key not in blocked
    }
    environment["PATH"] = (
        str(Path(_windows_system_directory()))
        if sys.platform == "win32"
        else "/usr/bin:/bin"
    )
    return environment
