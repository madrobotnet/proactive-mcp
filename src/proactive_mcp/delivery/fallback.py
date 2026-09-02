"""One-shot OS notification fallback for situations no agent received."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, TypeAlias

from proactive_mcp.config import FallbackSettings
from proactive_mcp.delivery.notify import NotificationError, send_os_notification
from proactive_mcp.delivery.payload import notification_payload
from proactive_mcp.store import FallbackClaim

if TYPE_CHECKING:
    from datetime import datetime

    from proactive_mcp.delivery.notify import NotificationErrorCode, NotificationHost
    from proactive_mcp.store import FallbackFailureCode, FallbackStore, Situation

__all__ = [
    "FallbackDispatch",
    "FallbackDispatcher",
    "FallbackFailed",
    "FallbackSent",
]


@dataclass(frozen=True, slots=True)
class FallbackSent:
    """One claimed situation whose single OS notification was sent."""

    situation_id: int


@dataclass(frozen=True, slots=True)
class FallbackFailed:
    """One claimed situation whose single OS notification failed."""

    situation_id: int
    failure_code: FallbackFailureCode


FallbackDispatch: TypeAlias = FallbackSent | FallbackFailed


class FallbackDispatcher:
    """Raise at most one OS notification per unreceived situation.

    Each eligible situation is claimed in its own committed transaction
    before its notification runs, so a failed or crashed send is recorded
    once and never retried. Delivery stays the agents' job: a notified
    situation stays pending and ``proactive_check`` may still deliver it.
    """

    _fallbacks: FallbackStore
    _host: NotificationHost
    _settings: FallbackSettings

    def __init__(
        self,
        fallbacks: FallbackStore,
        host: NotificationHost,
        settings: FallbackSettings | None = None,
    ) -> None:
        """Bind fallback claiming to one notification host and policy."""
        self._fallbacks = fallbacks
        self._host = host
        self._settings = settings if settings is not None else FallbackSettings()

    def dispatch(self, now: datetime) -> tuple[FallbackDispatch, ...]:
        """Notify each situation whose configured wait elapsed by ``now``."""
        if not self._settings.enabled:
            return ()
        dispatched: list[FallbackDispatch] = []
        while True:
            claimed = self._fallbacks.claim_next(self._claim(now))
            if claimed is None:
                return tuple(dispatched)
            dispatched.append(self._notify(claimed))

    def _claim(self, now: datetime) -> FallbackClaim:
        return FallbackClaim(
            claimed_at=_utc_iso(now),
            detected_before=_utc_iso(now - self._settings.wait),
            priorities=self._settings.priorities,
        )

    def _notify(self, situation: Situation) -> FallbackDispatch:
        try:
            send_os_notification(notification_payload(situation), self._host)
        except NotificationError as error:
            failure_code = _failure_code(error.error_code)
            self._fallbacks.record_failed(situation.id, code=failure_code)
            return FallbackFailed(situation.id, failure_code)
        self._fallbacks.record_sent(situation.id)
        return FallbackSent(situation.id)


def _failure_code(error_code: NotificationErrorCode) -> FallbackFailureCode:
    """Map one notification error onto its persisted failure code."""
    # Exhaustive over NotificationErrorCode: a new code breaks this match at
    # type-check time rather than silently recording an unknown failure.
    match error_code:
        case "timeout":
            return "timeout"
        case "unavailable":
            return "tool_missing"
        case "failed":
            return "nonzero_exit"
        case "unsupported_platform":
            return "unsupported_platform"


def _utc_iso(value: datetime) -> str:
    """Serialize one aware datetime as a lexicographic UTC ISO string."""
    return value.astimezone(UTC).isoformat()
