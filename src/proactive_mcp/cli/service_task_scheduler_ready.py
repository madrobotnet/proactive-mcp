"""Exact daemon-readiness signal for Windows Task Scheduler startup."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import PurePath

READY_FILE_ENV: Final = "PROACTIVE_SERVICE_READY_FILE"


def task_scheduler_ready_file(database: PurePath) -> PurePath:
    """Return the profile-bound readiness file for one daemon database."""
    return database.with_name(f"{database.name}.service-ready")


def signal_task_scheduler_ready(database: Path) -> None:
    """Create the expected readiness file after heartbeat ownership is stored."""
    configured = os.environ.get(READY_FILE_ENV)
    if configured is None:
        return
    expected = database.with_name(f"{database.name}.service-ready")
    if Path(configured) != expected:
        return
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(expected, flags, 0o600)
    try:
        _ = os.write(descriptor, str(os.getpid()).encode("ascii"))
    finally:
        os.close(descriptor)
