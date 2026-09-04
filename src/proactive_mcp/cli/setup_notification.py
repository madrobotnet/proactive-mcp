"""Fixed PII-free OS notification sent at the end of interactive setup."""

from __future__ import annotations

import sys
from typing import Final, Literal, TypeAlias

from proactive_mcp.delivery.notify import (
    NotificationError,
    NotificationErrorCode,
    NotificationHost,
    SubprocessNotificationRunner,
    notification_available,
    parse_notification_platform,
    send_os_notification,
)
from proactive_mcp.delivery.payload import NotificationPayload

__all__ = [
    "SetupNotificationOutcome",
    "emit_interactive_setup_notification",
    "send_setup_test_notification",
]

SetupNotificationOutcome: TypeAlias = Literal["ok"] | NotificationErrorCode
_TITLE: Final = "proactive-mcp"
_BODY: Final = "Setup test notification"


def send_setup_test_notification() -> SetupNotificationOutcome:
    """Send one fixed setup test toast.

    Takes no user, Situation, Gmail, Calendar, OAuth, or path data.
    Returns ``ok`` or a redacted ``NotificationErrorCode``. Never raises.
    """
    if not notification_available():
        return "unavailable"
    try:
        host = NotificationHost(
            parse_notification_platform(sys.platform),
            SubprocessNotificationRunner(),
        )
        send_os_notification(
            NotificationPayload(situation_type=_BODY, title=_TITLE),
            host,
        )
    except NotificationError as error:
        return error.error_code
    return "ok"


def emit_interactive_setup_notification() -> None:
    """Send the setup test notification and advise on redacted failure."""
    match send_setup_test_notification():
        case "ok":
            return
        case "unavailable" | "timeout" | "failed" | "unsupported_platform" as code:
            _ = sys.stderr.write(f"warning: setup test notification {code}\n")
