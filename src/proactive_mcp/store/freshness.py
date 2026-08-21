"""Pure freshness evaluation for persisted source synchronization state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from datetime import datetime

    from .sync import SourceAuthState, SourceErrorCode, SourceSyncState

SourceFreshnessStatus = Literal[
    "not_configured",
    "never_synced",
    "ok",
    "stale",
    "error",
    "needs_reauth",
]

DEFAULT_STALE_AFTER: Final = timedelta(hours=24)
_AUTH_STATUSES: Final[dict[SourceAuthState, SourceFreshnessStatus | None]] = {
    "not_configured": "not_configured",
    "configured": None,
    "needs_reauth": "needs_reauth",
}


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """The current user-visible freshness evaluation for one source."""

    status: SourceFreshnessStatus
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    age_seconds: int | None
    error_code: SourceErrorCode | None


def evaluate_source_freshness(
    state: SourceSyncState,
    now: datetime,
    *,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> SourceFreshness:
    """Evaluate one source state without reading wall-clock time."""
    auth_status = _AUTH_STATUSES[state.auth_state]
    if auth_status is not None:
        return _freshness(state, auth_status, None)
    if state.last_error_code is not None:
        return _freshness(state, "error", None)
    if state.last_success_at is None:
        return _freshness(state, "never_synced", None)
    age_seconds = int((now - state.last_success_at).total_seconds())
    status: SourceFreshnessStatus = (
        "stale" if age_seconds >= stale_after.total_seconds() else "ok"
    )
    return _freshness(state, status, age_seconds)


def _freshness(
    state: SourceSyncState,
    status: SourceFreshnessStatus,
    age_seconds: int | None,
) -> SourceFreshness:
    return SourceFreshness(
        status=status,
        last_success_at=state.last_success_at,
        last_attempt_at=state.last_attempt_at,
        age_seconds=age_seconds,
        error_code=state.last_error_code,
    )
