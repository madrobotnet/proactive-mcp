"""Date-anchor validation for memory MCP requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from proactive_mcp.server.memory_requests import RememberRequest


@dataclass(frozen=True, slots=True)
class InvalidDateAnchorError(ValueError):
    """Raised when a memory date anchor is not a supported calendar date."""

    value: str

    def __post_init__(self) -> None:
        """Initialize the validation error without exposing other memory data."""
        ValueError.__init__(
            self,
            "date_anchor must be an ISO date or --MM-DD",
        )


@dataclass(frozen=True, slots=True)
class MissingYearlyDateError(ValueError):
    """Raised when yearly recurrence has no date anchor."""

    def __post_init__(self) -> None:
        """Initialize the validation error."""
        ValueError.__init__(self, "yearly recurrence requires date_anchor")


@dataclass(frozen=True, slots=True)
class YearlessDateRequiresYearlyError(ValueError):
    """Raised when a yearless date is not configured to recur yearly."""

    def __post_init__(self) -> None:
        """Initialize the validation error."""
        ValueError.__init__(self, "yearless date_anchor requires yearly recurrence")


def validate_memory_date(memory: RememberRequest) -> None:
    """Reject date anchors that are invalid or incompatible with recurrence."""
    value = memory.date_anchor
    if value is None:
        if memory.recurrence == "yearly":
            raise MissingYearlyDateError
        return
    normalized = f"2000-{value.removeprefix('--')}" if value.startswith("--") else value
    try:
        _ = date.fromisoformat(normalized)
    except ValueError:
        raise InvalidDateAnchorError(value) from None
    if value.startswith("--") and memory.recurrence != "yearly":
        raise YearlessDateRequiresYearlyError


__all__ = [
    "InvalidDateAnchorError",
    "MissingYearlyDateError",
    "YearlessDateRequiresYearlyError",
    "validate_memory_date",
]
