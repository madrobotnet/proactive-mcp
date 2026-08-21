"""Deterministic attention policy: quiet hours, budget, cooldown, dedupe."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Final

from proactive_mcp.config import AttentionSettings

from ._dates import local_day_end, local_day_start

if TYPE_CHECKING:
    from datetime import time, tzinfo

    from proactive_mcp.store import Situation, SituationStore

__all__ = ["AttentionPolicy", "is_quiet_time"]

_PRIORITY_RANK: Final[dict[str, int]] = {"critical": 0, "high": 1, "routine": 2}


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
        local_now = now.astimezone(self._tz)
        if is_quiet_time(
            local_now.time(),
            self._settings.quiet_hours_start,
            self._settings.quiet_hours_end,
        ):
            return tuple(critical)
        remaining = self._remaining_budget(now)
        return tuple(critical + others[:remaining])

    def _remaining_budget(self, now: datetime) -> int:
        local_today = now.astimezone(self._tz).date()
        used = self._situations.count_delivered_between(
            local_day_start(local_today, self._tz),
            local_day_end(local_today, self._tz),
        )
        return max(0, self._settings.daily_budget - used)

    def _cooling_down(self, situation: Situation, now: datetime) -> bool:
        if situation.delivered_at is None:
            return False
        if situation.snoozed_until is not None:
            # The user explicitly re-scheduled this situation via snooze
            # after its last delivery, so the re-delivery cooldown yields.
            return False
        delivered_at = datetime.fromisoformat(situation.delivered_at)
        return delivered_at > now - self._settings.cooldown


def _delivery_order(situation: Situation) -> tuple[int, str, int]:
    return (
        _PRIORITY_RANK[situation.priority],
        situation.detected_at,
        situation.id,
    )
