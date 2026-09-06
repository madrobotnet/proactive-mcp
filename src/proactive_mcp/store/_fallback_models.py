"""Typed one-shot OS notification fallback claims and outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import TypeAdapter

from ._situation_models import (
    SituationPriority,  # noqa: TC001 - Pydantic resolves this annotation at runtime.
)

FallbackClaimMode = Literal["configured", "bootstrap"]
FallbackOutcome = Literal["claimed", "sent", "failed"]
FallbackTerminalOutcome = Literal["sent", "failed"]
FallbackFailureCode = Literal[
    "unsupported_platform",
    "tool_missing",
    "nonzero_exit",
    "timeout",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class FallbackClaim:
    """The configured policy values one fallback claim attempt needs.

    ``detected_before`` is the newest detection instant still eligible, so
    the caller owns the wait window and this store owns the atomicity.
    """

    claimed_at: str
    detected_before: str
    priorities: tuple[SituationPriority, ...]
    mode: FallbackClaimMode = "configured"


@dataclass(frozen=True, slots=True)
class FallbackRecord:
    """One immutable fallback claim and its single terminal outcome."""

    situation_id: int
    priority: SituationPriority
    outcome: FallbackOutcome
    failure_code: FallbackFailureCode | None
    claimed_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class FallbackSummary:
    """Redacted outcome counts across every persisted fallback record."""

    claimed: int
    sent: int
    failed: int
    failure_codes: tuple[FallbackFailureCode, ...]


FALLBACK_RECORD_ADAPTER: Final[TypeAdapter[FallbackRecord]] = TypeAdapter(
    FallbackRecord
)
FALLBACK_SUMMARY_ADAPTER: Final[TypeAdapter[FallbackSummary]] = TypeAdapter(
    FallbackSummary
)


@dataclass(frozen=True, slots=True)
class FallbackNotClaimedError(Exception):
    """Raised when an outcome is recorded without an open fallback claim."""

    id: int

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"situation {self.id} has no open fallback claim")
