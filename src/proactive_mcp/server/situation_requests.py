"""Parsed MCP request values for the situation delivery tools."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAlias

MuteScope: TypeAlias = Literal["instance", "type"]

__all__ = ["MuteScope", "SituationRequestError", "parse_snooze_until"]


class SituationRequestError(Exception):
    """Raised when a tool argument is unusable; the value is never echoed.

    Written as a plain exception rather than a frozen slots dataclass:
    such dataclasses reject the ``__traceback__`` assignment that unwinding
    performs, which would replace this error with a TypeError.
    """

    field: str
    reason: str

    def __init__(self, field: str, reason: str) -> None:
        """Build the boundary-safe message this error reports."""
        super().__init__(f"invalid {field}: {reason}")
        self.field = field
        self.reason = reason


def parse_snooze_until(raw: str, now: datetime) -> datetime:
    """Parse one wake time into a timezone-aware instant after ``now``."""
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        raise SituationRequestError(
            field="until",
            reason="must be an ISO-8601 timestamp",
        ) from None
    if until.tzinfo is None or until.tzinfo.utcoffset(until) is None:
        raise SituationRequestError(field="until", reason="must carry a UTC offset")
    if until <= now:
        raise SituationRequestError(field="until", reason="must be in the future")
    return until
