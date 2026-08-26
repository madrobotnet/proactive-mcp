"""Systemd user-unit rendering for the managed watcher service."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import PurePath

__all__ = [
    "MANAGED_UNIT_MARKER",
    "UnsafeServiceValueError",
    "is_managed_unit",
    "render_user_unit",
]

MANAGED_UNIT_MARKER: Final = "X-Proactive-MCP-Managed=true"
_SAFE_UNIT_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-"
)


class UnsafeServiceValueError(ValueError):
    """Signal that a value cannot safely cross into systemd syntax."""


def render_user_unit(executable: PurePath, database: PurePath) -> str:
    """Render one profile-bound systemd user unit."""
    executable_value = _validated(str(executable))
    database_value = _validated(str(database))
    return f"""[Unit]
Description=proactive-mcp watcher
After=network-online.target
Wants=network-online.target
{MANAGED_UNIT_MARKER}

[Service]
Type=notify
ExecStart={_quote(executable_value)} daemon
Environment=\"{_escape(f"PROACTIVE_DATABASE={database_value}")}\"
Restart=on-failure
RestartSec=5s
RestartPreventExitStatus=2
TimeoutStartSec=30s
NotifyAccess=main
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""


def is_managed_unit(content: str) -> bool:
    """Return whether the unit carries this package's ownership marker."""
    return MANAGED_UNIT_MARKER in content.splitlines()


def _validated(value: str) -> str:
    if any(unicodedata.category(character) in ("Cc", "Cf") for character in value):
        raise UnsafeServiceValueError
    return value


def _quote(value: str) -> str:
    escaped = _escape(value)
    if all(character in _SAFE_UNIT_CHARS for character in value):
        return escaped
    return f'"{escaped}"'


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
