"""Profile-scoped observations of host-owned MCP delivery calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

CollectorProfile = Literal["full", "scheduled"]
CollectorState = Literal["never_seen", "active", "stale"]
DEFAULT_COLLECTOR_STALE_AFTER = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class CollectorStatus:
    """One profile's observable host-call activity."""

    profile: CollectorProfile
    state: CollectorState
    last_check_at: str | None
    last_confirm_at: str | None


class CollectorStore:
    """Persist only bounded timestamps, never host or conversation identity."""

    _connection: sqlite3.Connection
    _clock: Clock

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind collector observations to one connection and clock."""
        self._connection = connection
        self._clock = clock

    def record_check(self, profile: CollectorProfile) -> None:
        """Observe one profile's proactive-check call."""
        timestamp = self._clock.now().astimezone(UTC).isoformat()
        _ = self._connection.execute(
            """
            INSERT INTO collector_observations(profile, last_check_at)
            VALUES (?, ?)
            ON CONFLICT(profile) DO UPDATE SET
                last_check_at = CASE
                    WHEN collector_observations.last_check_at IS NULL
                      OR excluded.last_check_at > collector_observations.last_check_at
                    THEN excluded.last_check_at
                    ELSE collector_observations.last_check_at
                END
            """,
            (profile, timestamp),
        )

    def record_confirm(self, profile: CollectorProfile) -> None:
        """Observe one successful profile confirmation."""
        timestamp = self._clock.now().astimezone(UTC).isoformat()
        _ = self._connection.execute(
            """
            INSERT INTO collector_observations(
                profile, last_check_at, last_confirm_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(profile) DO UPDATE SET
                last_confirm_at = CASE
                    WHEN collector_observations.last_confirm_at IS NULL
                      OR excluded.last_confirm_at
                         > collector_observations.last_confirm_at
                    THEN excluded.last_confirm_at
                    ELSE collector_observations.last_confirm_at
                END
            """,
            (profile, timestamp, timestamp),
        )

    def status(
        self,
        profile: CollectorProfile,
        *,
        stale_after: timedelta = DEFAULT_COLLECTOR_STALE_AFTER,
    ) -> CollectorStatus:
        """Return whether this profile has been observed recently."""
        row = cast(
            "tuple[str, str | None] | None",
            self._connection.execute(
                """
                SELECT last_check_at, last_confirm_at
                FROM collector_observations
                WHERE profile = ?
                """,
                (profile,),
            ).fetchone(),
        )
        if row is None:
            return CollectorStatus(profile, "never_seen", None, None)
        last_check_at, last_confirm_at = row
        check_time = datetime.fromisoformat(last_check_at)
        state: CollectorState = (
            "active" if self._clock.now() - check_time <= stale_after else "stale"
        )
        return CollectorStatus(profile, state, last_check_at, last_confirm_at)
