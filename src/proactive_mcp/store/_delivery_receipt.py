"""Digest-only persistence for active and confirmed delivery receipts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from ._situation_models import DeliveryConfirmation

if TYPE_CHECKING:
    import sqlite3


def receipt_digest(receipt_token: str) -> bytes:
    """Minimize one untrusted receipt to its fixed-size lookup digest."""
    return sha256(receipt_token.encode()).digest()


@dataclass(frozen=True, slots=True)
class DeliveryReceipts:
    """Persist active and immutable confirmed delivery receipts."""

    connection: sqlite3.Connection

    def expire(self, timestamp: str) -> None:
        """Delete active receipt leases expired by a claim timestamp."""
        _ = self.connection.execute(
            "DELETE FROM situation_delivery_claims WHERE expires_at <= ?",
            (timestamp,),
        )

    def consume(self, digest: bytes) -> None:
        """Delete an active receipt lease after whole-lease delivery."""
        _ = self.connection.execute(
            "DELETE FROM situation_delivery_claims WHERE receipt_digest = ?",
            (digest,),
        )

    def replay(self, digest: bytes) -> DeliveryConfirmation | None:
        """Return the immutable result for a previously confirmed receipt."""
        receipt = cast(
            "tuple[int] | None",
            self.connection.execute(
                """
                SELECT delivered_count FROM confirmed_delivery_receipts
                WHERE receipt_digest = ?
                """,
                (digest,),
            ).fetchone(),
        )
        if receipt is None:
            return None
        return DeliveryConfirmation("already_confirmed", receipt[0])

    def record(
        self,
        digest: bytes,
        delivered_count: int,
        confirmed_at: str,
    ) -> DeliveryConfirmation:
        """Append and return one immutable confirmation result."""
        _ = self.connection.execute(
            """
            INSERT INTO confirmed_delivery_receipts(
                receipt_digest, delivered_count, confirmed_at
            ) VALUES (?, ?, ?)
            """,
            (digest, delivered_count, confirmed_at),
        )
        return DeliveryConfirmation("confirmed", delivered_count)
