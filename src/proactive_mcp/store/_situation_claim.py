"""Atomic SQLite delivery claiming and immutable history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._situation_models import Situation, SituationNotFoundError
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3

    from ._situation_models import DeliveryClaim
    from ._situation_reader import SituationReader


def claim_for_delivery(
    connection: sqlite3.Connection,
    reader: SituationReader,
    claim: DeliveryClaim,
) -> tuple[Situation, ...]:
    """Atomically enforce suppression and claim only rows this call owns."""
    claimed: list[Situation] = []
    with ImmediateTransaction(connection):
        for candidate in reader.list_situations("pending"):
            if not claim.allow_noncritical and candidate.priority != "critical":
                continue
            cursor = connection.execute(
                """
                UPDATE situations
                SET state = 'delivered', delivered_at = ?, updated_at = ?,
                    snoozed_until = NULL, snooze_cooldown_exempt = 0
                WHERE id = ? AND state = 'pending'
                  AND NOT EXISTS (
                      SELECT 1 FROM situation_type_mutes
                      WHERE situation_type = situations.situation_type
                  )
                  AND (
                      snooze_cooldown_exempt = 1 OR NOT EXISTS (
                          SELECT 1 FROM situation_deliveries
                          WHERE situation_id = situations.id
                            AND delivered_at > ?
                      )
                  )
                  AND (priority = 'critical' OR (
                      SELECT COUNT(*) FROM situation_deliveries
                      WHERE delivered_at >= ? AND delivered_at < ?
                        AND priority != 'critical'
                  ) < ?)
                """,
                (
                    claim.delivered_at,
                    claim.delivered_at,
                    candidate.id,
                    claim.cooldown_after,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.daily_budget,
                ),
            )
            if cursor.rowcount == 0:
                continue
            situation = reader.get_situation(candidate.id)
            if situation is None:
                raise SituationNotFoundError(candidate.id)
            record_delivery(connection, situation, claim.delivered_at)
            claimed.append(situation)
    return tuple(claimed)


def record_delivery(
    connection: sqlite3.Connection,
    situation: Situation,
    timestamp: str,
) -> None:
    """Append immutable claim-time priority history."""
    _ = connection.execute(
        """
        INSERT INTO situation_deliveries (situation_id, delivered_at, priority)
        VALUES (?, ?, ?)
        """,
        (situation.id, timestamp, situation.priority),
    )
