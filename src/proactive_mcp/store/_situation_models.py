"""Typed situation records, detections, and state machine errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from datetime import datetime

SituationType = Literal["reply_deadline", "calendar_conflict", "personal_occasion"]
SituationState = Literal[
    "pending",
    "delivered",
    "acknowledged",
    "snoozed",
    "muted",
    "resolved",
    "expired",
]
SituationPriority = Literal["critical", "high", "routine"]

ACTIVE_SITUATION_STATES: Final[tuple[SituationState, ...]] = (
    "pending",
    "delivered",
    "snoozed",
)


@dataclass(frozen=True, slots=True)
class SituationEvidence:
    """Grounding for one situation with untrusted external text isolated.

    ``facts`` holds structural values the engine derived itself (ids, times,
    counts). ``quoted_external`` holds text quoted from external sources
    (email subjects, senders, event titles). ``quoted_memory`` holds prose
    persisted by an MCP client. Both are data, never instructions.
    """

    facts: dict[str, str] = field(default_factory=dict)
    quoted_external: dict[str, str] = field(default_factory=dict)
    quoted_memory: dict[str, str] = field(default_factory=dict)
    contradictory_dates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Detection:
    """One deterministic detector finding, ready to persist."""

    situation_type: SituationType
    dedupe_key: str
    priority: SituationPriority
    title: str
    why_now: str
    evidence: SituationEvidence
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Situation:
    """A stored situation with its full delivery state machine timestamps."""

    id: int
    situation_type: SituationType
    dedupe_key: str
    state: SituationState
    priority: SituationPriority
    title: str
    why_now: str
    evidence: SituationEvidence
    expires_at: str | None
    detected_at: str
    updated_at: str
    delivered_at: str | None
    acknowledged_at: str | None
    snoozed_until: str | None
    resolved_at: str | None
    expired_at: str | None
    muted_at: str | None


@dataclass(frozen=True, slots=True)
class DetectionUpsertSummary:
    """Deduplicated outcome counts of persisting one detection batch."""

    created: int
    reactivated: int
    refreshed: int
    skipped: int
    capacity_skipped: int = 0


@dataclass(frozen=True, slots=True)
class DetectionApplySummary:
    """Atomic detection upsert and allowed-resolution outcome."""

    upsert: DetectionUpsertSummary
    resolved: int


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """All policy values needed for one atomic delivery claim."""

    delivered_at: str
    cooldown_after: str
    local_day_start: str
    local_day_end: str
    daily_budget: int
    allow_noncritical: bool


@dataclass(frozen=True, slots=True)
class DeliveryReservation:
    """Pending situations leased to one host until it confirms receipt."""

    claim_token: str
    situations: tuple[Situation, ...]
    expires_at: str


SITUATION_ADAPTER: Final[TypeAdapter[Situation]] = TypeAdapter(Situation)
SITUATION_EVIDENCE_ADAPTER: Final[TypeAdapter[SituationEvidence]] = TypeAdapter(
    SituationEvidence
)


@dataclass(frozen=True, slots=True)
class SituationNotFoundError(Exception):
    """Raised when a situation does not exist."""

    id: int

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"situation {self.id} not found")


@dataclass(frozen=True, slots=True)
class InvalidSituationTransitionError(Exception):
    """Raised when a state transition is not allowed by the state machine."""

    id: int
    state: SituationState
    action: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        message = (
            f"situation {self.id} in state {self.state!r}"
            f" does not allow {self.action!r}"
        )
        Exception.__init__(self, message)


@dataclass(frozen=True, slots=True)
class SituationValidationError(Exception):
    """Raised when a situation value cannot be represented by the model."""

    field: str
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"invalid situation {self.field}: {self.reason}")


@dataclass(frozen=True, slots=True)
class DeliveryReceiptError(Exception):
    """Raised when a receipt token is missing, expired, or already consumed."""

    def __post_init__(self) -> None:
        Exception.__init__(self, "delivery receipt is invalid or expired")
