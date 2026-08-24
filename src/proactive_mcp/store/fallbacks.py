"""Atomic one-shot claiming and outcome recording for OS notification fallbacks."""

from __future__ import annotations

import json
from datetime import UTC
from typing import TYPE_CHECKING

from ._fallback_models import (
    FALLBACK_RECORD_ADAPTER,
    FallbackNotClaimedError,
    FallbackRecord,
)
from ._fallback_sql import (
    INSERT_FALLBACK_CLAIM,
    RECORD_FALLBACK_OUTCOME,
    SELECT_FALLBACK_CANDIDATES,
    SELECT_FALLBACK_RECORD,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

    from ._fallback_models import (
        FallbackClaim,
        FallbackFailureCode,
        FallbackTerminalOutcome,
    )
    from ._situation_models import Situation
    from ._situation_reader import SituationReader

__all__ = ["FallbackNotClaimedError", "FallbackStore"]


class FallbackStore:
    """Own which situations may raise one OS notification, and only once.

    The capture buffer is mutable because SQLite scalar callbacks cannot
    return structured Python records directly.
    """

    _connection: sqlite3.Connection
    _clock: Clock
    _situations: SituationReader
    _records: list[FallbackRecord]

    def __init__(
        self,
        connection: sqlite3.Connection,
        clock: Clock,
        situations: SituationReader,
    ) -> None:
        """Bind fallback claiming to a connection, clock, and situation reader."""
        self._connection = connection
        self._clock = clock
        self._situations = situations
        self._records = []
        connection.create_function(
            "_proactive_capture_fallback_record",
            1,
            self._capture_record,
        )

    def candidates(self, claim: FallbackClaim) -> tuple[Situation, ...]:
        """Return eligible unclaimed rows, most urgent first."""
        return self._situations.capture_situations(
            SELECT_FALLBACK_CANDIDATES,
            _eligibility(claim),
        )

    def claim_next(self, claim: FallbackClaim) -> Situation | None:
        """Atomically claim the most urgent eligible row before sending it.

        The claim is written before the notification subprocess runs, so a
        crashed send is never retried and a lost claim is never re-offered.
        """
        with ImmediateTransaction(self._connection):
            for candidate in self.candidates(claim):
                cursor = self._connection.execute(
                    INSERT_FALLBACK_CLAIM,
                    (claim.claimed_at, candidate.id, *_eligibility(claim)),
                )
                if cursor.rowcount != 0:
                    return candidate
        return None

    def record_sent(self, situation_id: int) -> None:
        """Record that one claimed fallback notification was sent."""
        self._complete(situation_id, "sent", None)

    def record_failed(
        self,
        situation_id: int,
        *,
        code: FallbackFailureCode,
    ) -> None:
        """Record that one claimed fallback notification failed."""
        self._complete(situation_id, "failed", code)

    def history(self, situation_id: int) -> FallbackRecord | None:
        """Return the immutable fallback record of one situation, if any."""
        self._records.clear()
        _ = self._connection.execute(SELECT_FALLBACK_RECORD, (situation_id,))
        return self._records[0] if self._records else None

    def _complete(
        self,
        situation_id: int,
        outcome: FallbackTerminalOutcome,
        failure_code: FallbackFailureCode | None,
    ) -> None:
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                RECORD_FALLBACK_OUTCOME,
                (outcome, failure_code, self._now_iso(), situation_id),
            )
            if cursor.rowcount == 0:
                raise FallbackNotClaimedError(situation_id)

    def _capture_record(self, payload: str) -> int:
        record = FALLBACK_RECORD_ADAPTER.validate_json(payload)
        self._records.append(record)
        return record.situation_id

    def _now_iso(self) -> str:
        return self._clock.now().astimezone(UTC).isoformat()


def _eligibility(claim: FallbackClaim) -> tuple[str, str, str, str]:
    """Bind the fallback eligibility parameters in their SQL order."""
    return (
        json.dumps(list(claim.priorities)),
        claim.detected_before,
        claim.claimed_at,
        claim.claimed_at,
    )
