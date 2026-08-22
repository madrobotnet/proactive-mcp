"""OS notification fallback via argv-only subprocess calls."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias

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
    "parse_notification_platform",
    "send_os_notification",
]

NotificationErrorCode: TypeAlias = Literal[
    "timeout",
    "unavailable",
    "failed",
    "unsupported_platform",
]
NotificationPlatform: TypeAlias = Literal["linux", "darwin", "win32"]

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
            )
        except FileNotFoundError:
            raise NotificationError(_UNAVAILABLE) from None
        except subprocess.TimeoutExpired:
            raise NotificationError(_TIMEOUT) from None
        if completed.returncode != 0:
            raise NotificationError(_FAILED)


def parse_notification_platform(raw: str) -> NotificationPlatform:
    """Parse a sys.platform value into a supported notification platform."""
    try:
        return _PLATFORMS[raw]
    except KeyError:
        raise NotificationError(_UNSUPPORTED) from None


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
        "linux": ("notify-send", "--", title, body),
        "darwin": (
            "osascript",
            str(MACOS_NOTIFICATION_SCRIPT),
            title,
            body,
        ),
        "win32": (
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(WINDOWS_TOAST_SCRIPT),
            title,
            body,
        ),
    }
    return commands[platform]
