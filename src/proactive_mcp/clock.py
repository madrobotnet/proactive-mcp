"""UTC clock abstractions for deterministic time-dependent behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provide the current UTC time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class UtcClock:
    """Read the current time from the system UTC clock."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)
