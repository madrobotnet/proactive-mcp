"""Atomic SQLite delivery claiming and immutable history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ._delivery_eligibility import (
    CURRENT_SOURCE_ELIGIBILITY,
    reserved_non_reply_slots_for_claim,
    reserved_non_reply_slots_for_reservation,
)
from ._delivery_receipt import DeliveryReceipts, receipt_digest
from ._situation_models import (
    DeliveryConfirmation,
    DeliveryReceiptError,
    DeliveryReservation,
    Situation,
    SituationNotFoundError,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3

    from ._situation_models import DeliveryClaim
    from ._situation_reader import SituationReader

_MAX_DELIVERY_CANDIDATES: Final[int] = 100


def claim_for_delivery(
    connection: sqlite3.Connection, reader: SituationReader, claim: DeliveryClaim
) -> tuple[Situation, ...]:
    """Atomically enforce suppression and claim only rows this call owns."""
    claimed: list[Situation] = []
    with ImmediateTransaction(connection):
        candidates = reader.pending_for_delivery(
            limit=_MAX_DELIVERY_CANDIDATES,
        )
        reserved_non_reply_slots = reserved_non_reply_slots_for_claim(
            reader,
            candidates,
            claim,
        )
        for candidate in candidates:
            if not claim.allow_noncritical and candidate.priority != "critical":
                continue
            cursor = connection.execute(
                f"""
                UPDATE situations
                SET state = 'delivered', delivered_at = ?, updated_at = ?,
                    snoozed_until = NULL, snooze_cooldown_exempt = 0
                WHERE id = ? AND state = 'pending'
                  AND (expires_at IS NULL OR expires_at > ?)
                  {CURRENT_SOURCE_ELIGIBILITY}
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
                  AND (priority = 'critical' OR situation_type != 'reply_deadline'
                       OR (
                           SELECT COUNT(*) FROM situation_deliveries deliveries
                           JOIN situations delivered
                             ON delivered.id = deliveries.situation_id
                           WHERE deliveries.delivered_at >= ?
                             AND deliveries.delivered_at < ?
                             AND deliveries.priority != 'critical'
                             AND delivered.situation_type = 'reply_deadline'
                       ) < MAX(0, ? - ?))
                """,  # noqa: S608
                (
                    claim.delivered_at,
                    claim.delivered_at,
                    candidate.id,
                    claim.delivered_at,
                    claim.cooldown_after,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.daily_budget,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.daily_budget,
                    reserved_non_reply_slots,
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


def reserve_for_delivery(
    connection: sqlite3.Connection,
    reader: SituationReader,
    claim: DeliveryClaim,
    *,
    claim_token: str,
    expires_at: str,
) -> DeliveryReservation:
    """Lease bounded pending rows without recording delivery history."""
    digest = receipt_digest(claim_token)
    receipts = DeliveryReceipts(connection)
    reserved: list[Situation] = []
    with ImmediateTransaction(connection):
        receipts.expire(claim.delivered_at)
        candidates = reader.pending_for_delivery(limit=_MAX_DELIVERY_CANDIDATES)
        reserved_non_reply_slots = reserved_non_reply_slots_for_reservation(
            reader,
            candidates,
            claim,
        )
        for candidate in candidates:
            if not claim.allow_noncritical and candidate.priority != "critical":
                continue
            cursor = connection.execute(
                f"""
                INSERT INTO situation_delivery_claims(
                    receipt_digest, situation_id, claimed_at, expires_at
                )
                SELECT ?, id, ?, ? FROM situations
                WHERE id = ? AND state = 'pending'
                  AND (expires_at IS NULL OR expires_at > ?)
                  {CURRENT_SOURCE_ELIGIBILITY}
                  AND NOT EXISTS (
                      SELECT 1 FROM situation_delivery_claims
                      WHERE situation_id = situations.id
                  )
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
                      (SELECT COUNT(*) FROM situation_deliveries
                       WHERE delivered_at >= ? AND delivered_at < ?
                         AND priority != 'critical')
                      +
                      (SELECT COUNT(*) FROM situation_delivery_claims claims
                       JOIN situations claimed ON claimed.id = claims.situation_id
                       WHERE claims.claimed_at >= ? AND claims.claimed_at < ?
                         AND claims.expires_at > ?
                         AND claimed.priority != 'critical')
                  ) < ?)
                  AND (priority = 'critical' OR situation_type != 'reply_deadline'
                       OR (
                           (SELECT COUNT(*)
                            FROM situation_deliveries deliveries
                            JOIN situations delivered
                              ON delivered.id = deliveries.situation_id
                            WHERE deliveries.delivered_at >= ?
                              AND deliveries.delivered_at < ?
                              AND deliveries.priority != 'critical'
                              AND delivered.situation_type = 'reply_deadline')
                           +
                           (SELECT COUNT(*) FROM situation_delivery_claims claims
                            JOIN situations claimed
                              ON claimed.id = claims.situation_id
                            WHERE claims.claimed_at >= ? AND claims.claimed_at < ?
                              AND claims.expires_at > ?
                              AND claimed.priority != 'critical'
                              AND claimed.situation_type = 'reply_deadline')
                       ) < MAX(0, ? - ?))
                """,  # noqa: S608
                (
                    digest,
                    claim.delivered_at,
                    expires_at,
                    candidate.id,
                    claim.delivered_at,
                    claim.cooldown_after,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.delivered_at,
                    claim.daily_budget,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.local_day_start,
                    claim.local_day_end,
                    claim.delivered_at,
                    claim.daily_budget,
                    reserved_non_reply_slots,
                ),
            )
            if cursor.rowcount:
                reserved.append(candidate)
    return DeliveryReservation(claim_token, tuple(reserved), expires_at)


def confirm_delivery(
    connection: sqlite3.Connection,
    reader: SituationReader,
    claim_token: str,
    *,
    confirmed_at: str,
) -> DeliveryConfirmation:
    """Confirm one active lease or replay its immutable result."""
    digest = receipt_digest(claim_token)
    receipts = DeliveryReceipts(connection)
    with ImmediateTransaction(connection):
        replay = receipts.replay(digest)
        if replay is not None:
            return replay

        receipts.expire(confirmed_at)
        situation_ids = reader.delivery_claim_ids(digest)
        if not situation_ids:
            raise DeliveryReceiptError

        for situation_id in situation_ids:
            cursor = connection.execute(
                """
                UPDATE situations
                SET state = 'delivered', delivered_at = ?, updated_at = ?,
                    snoozed_until = NULL, snooze_cooldown_exempt = 0
                WHERE id = ? AND state = 'pending'
                """,
                (confirmed_at, confirmed_at, situation_id),
            )
            if cursor.rowcount == 0:
                raise DeliveryReceiptError
            situation = reader.get_situation(situation_id)
            if situation is None:
                raise SituationNotFoundError(situation_id)
            record_delivery(connection, situation, confirmed_at)

        receipts.consume(digest)
        return receipts.record(digest, len(situation_ids), confirmed_at)


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
