"""Systemd user-unit rendering for the managed watcher service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MANAGED_UNIT_MARKER", "is_managed_unit", "render_user_unit"]

MANAGED_UNIT_MARKER: Final = "X-Proactive-MCP-Managed=true"
_SAFE_UNIT_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-"
)


def render_user_unit(executable: Path, database: Path) -> str:
    """Render one profile-bound systemd user unit."""
    return f"""[Unit]
Description=proactive-mcp watcher
After=network-online.target
Wants=network-online.target
{MANAGED_UNIT_MARKER}

[Service]
Type=notify
ExecStart={_quote(executable)} daemon
Environment=\"PROACTIVE_DATABASE={_escape(str(database))}\"
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


def _quote(path: Path) -> str:
    value = str(path)
    escaped = _escape(value)
    if all(character in _SAFE_UNIT_CHARS for character in value):
        return escaped
    return f'"{escaped}"'


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
