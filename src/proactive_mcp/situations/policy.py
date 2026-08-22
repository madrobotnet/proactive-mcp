"""Deterministic attention policy: quiet hours, budget, cooldown, dedupe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from proactive_mcp.config import AttentionSettings
from proactive_mcp.store._situation_models import DeliveryClaim

from ._dates import local_day_end, local_day_start

if TYPE_CHECKING:
    from datetime import time, tzinfo

    from proactive_mcp.store import Situation, SituationStore

__all__ = ["AttentionPolicy", "BudgetUsage", "QuietState", "is_quiet_time"]

_PRIORITY_RANK: Final[dict[str, int]] = {"critical": 0, "high": 1, "routine": 2}


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Non-critical deliveries already claimed on the local calendar day."""

    used: int
    remaining: int
    daily_budget: int


@dataclass(frozen=True, slots=True)
class QuietState:
    """Whether quiet hours are active at one instant in the policy timezone."""

    active: bool


def is_quiet_time(local_time: time, start: time, end: time) -> bool:
    """Return whether one local wall-clock time falls inside quiet hours.

    Quiet hours begin exactly at ``start`` and end exactly at ``end``, and
    may cross midnight. Equal bounds disable quiet hours entirely.
    """
    if start == end:
        return False
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


class AttentionPolicy:
    """Select which pending situations may be delivered right now.

    Critical situations bypass quiet hours and never consume the daily
    budget; everything else is held during quiet hours, capped by the
    remaining budget, and suppressed while its dedupe key cools down.
    Held situations stay pending and carry over, highest priority first.
    """

    _situations: SituationStore
    _settings: AttentionSettings
    _tz: tzinfo

    def __init__(
        self,
        situations: SituationStore,
        tz: tzinfo,
        settings: AttentionSettings | None = None,
    ) -> None:
        """Bind the policy to situation storage, a timezone, and settings."""
        self._situations = situations
        self._settings = settings if settings is not None else AttentionSettings()
        self._tz = tz

    def select_for_delivery(self, now: datetime) -> tuple[Situation, ...]:
        """Return the situations deliverable at ``now``, critical first.

        The selection does not mark anything delivered; the caller owns
        that transition after the situations actually reach an agent.
        """
        muted_types = set(self._situations.muted_situation_types())
        candidates = [
            situation
            for situation in self._situations.list_situations(state="pending")
            if situation.situation_type not in muted_types
            and not self._cooling_down(situation, now)
        ]
        candidates.sort(key=_delivery_order)
        critical = [c for c in candidates if c.priority == "critical"]
        others = [c for c in candidates if c.priority != "critical"]
        if self.quiet_state(now).active:
            return tuple(critical)
        remaining = self.budget_usage(now).remaining
        return tuple(critical + others[:remaining])

    def claim_for_delivery(self, now: datetime) -> tuple[Situation, ...]:
        """Atomically claim deliverable rows under all attention limits."""
        utc_now = now.astimezone(UTC)
        local_today = now.astimezone(self._tz).date()
        return self._situations.claim_for_delivery(
            DeliveryClaim(
                delivered_at=_utc_iso(utc_now),
                cooldown_after=_utc_iso(utc_now - self._settings.cooldown),
                local_day_start=_utc_iso(local_day_start(local_today, self._tz)),
                local_day_end=_utc_iso(local_day_end(local_today, self._tz)),
                daily_budget=self._settings.daily_budget,
                allow_noncritical=not self.quiet_state(now).active,
            )
        )

    def budget_usage(self, now: datetime) -> BudgetUsage:
        """Return today's non-critical usage and remaining daily budget."""
        local_today = now.astimezone(self._tz).date()
        used = self._situations.count_delivered_between(
            local_day_start(local_today, self._tz),
            local_day_end(local_today, self._tz),
        )
        daily_budget = self._settings.daily_budget
        return BudgetUsage(
            used=used,
            remaining=max(0, daily_budget - used),
            daily_budget=daily_budget,
        )

    def quiet_state(self, now: datetime) -> QuietState:
        """Return whether quiet hours hold at ``now`` in the policy timezone."""
        local_now = now.astimezone(self._tz)
        return QuietState(
            active=is_quiet_time(
                local_now.time(),
                self._settings.quiet_hours_start,
                self._settings.quiet_hours_end,
            )
        )

    def _cooling_down(self, situation: Situation, now: datetime) -> bool:
        if situation.delivered_at is None:
            return False
        if situation.snoozed_until is not None:
            # The user explicitly re-scheduled this situation via snooze
            # after its last delivery, so the re-delivery cooldown yields.
            return False
        delivered_at = datetime.fromisoformat(situation.delivered_at)
        return delivered_at > now - self._settings.cooldown


def _utc_iso(value: datetime) -> str:
    """Serialize one instant as a lexicographic UTC ISO-8601 string."""
    return value.astimezone(UTC).isoformat()


def _delivery_order(situation: Situation) -> tuple[int, str, int]:
    return (
        _PRIORITY_RANK[situation.priority],
        situation.detected_at,
        situation.id,
    )
