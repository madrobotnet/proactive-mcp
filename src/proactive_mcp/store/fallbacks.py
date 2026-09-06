"""Atomic one-shot claiming and outcome recording for OS notification fallbacks."""

from __future__ import annotations

import json
from datetime import UTC
from typing import TYPE_CHECKING, assert_never

from ._fallback_models import (
    FALLBACK_RECORD_ADAPTER,
    FALLBACK_SUMMARY_ADAPTER,
    FallbackNotClaimedError,
    FallbackRecord,
    FallbackSummary,
)
from ._fallback_sql import (
    INSERT_BOOTSTRAP_FALLBACK_CLAIM,
    INSERT_FALLBACK_CLAIM,
    RECORD_FALLBACK_OUTCOME,
    SELECT_BOOTSTRAP_FALLBACK_CANDIDATES,
    SELECT_FALLBACK_CANDIDATES,
    SELECT_FALLBACK_RECORD,
    SELECT_FALLBACK_SUMMARY,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

    from ._fallback_models import (
        FallbackClaim,
        FallbackClaimMode,
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
    _summaries: list[FallbackSummary]

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
        self._summaries = []
        connection.create_function(
            "_proactive_capture_fallback_record",
            1,
            self._capture_record,
        )
        connection.create_function(
            "_proactive_capture_fallback_summary",
            1,
            self._capture_summary,
        )

    def candidates(self, claim: FallbackClaim) -> tuple[Situation, ...]:
        """Return rows eligible for the claim mode in its policy order."""
        candidate_sql, _ = _claim_sql(claim.mode)
        return self._situations.capture_situations(
            candidate_sql,
            _eligibility(claim),
        )

    def claim_next(self, claim: FallbackClaim) -> Situation | None:
        """Atomically claim the first row eligible for the selected mode.

        Configured mode keeps urgency and configured-priority ordering. Bootstrap
        mode ignores priority only when global delivery and fallback history are
        empty. Both select and insertion execute in one immediate transaction.
        """
        candidate_sql, insert_sql = _claim_sql(claim.mode)
        eligibility = _eligibility(claim)
        with ImmediateTransaction(self._connection):
            candidates = self._situations.capture_situations(
                candidate_sql,
                eligibility,
            )
            for candidate in candidates:
                cursor = self._connection.execute(
                    insert_sql,
                    (claim.claimed_at, candidate.id, *eligibility),
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

    def summary(self) -> FallbackSummary:
        """Return redacted outcome counts for every persisted fallback."""
        self._summaries.clear()
        _ = self._connection.execute(SELECT_FALLBACK_SUMMARY)
        return self._summaries[0]

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

    def _capture_summary(self, payload: str) -> int:
        summary = FALLBACK_SUMMARY_ADAPTER.validate_json(payload)
        self._summaries.append(summary)
        return summary.claimed + summary.sent + summary.failed

    def _now_iso(self) -> str:
        return self._clock.now().astimezone(UTC).isoformat()


def _claim_sql(mode: FallbackClaimMode) -> tuple[str, str]:
    """Select the candidate and insertion statements for one claim mode."""
    match mode:
        case "configured":
            return (SELECT_FALLBACK_CANDIDATES, INSERT_FALLBACK_CLAIM)
        case "bootstrap":
            return (
                SELECT_BOOTSTRAP_FALLBACK_CANDIDATES,
                INSERT_BOOTSTRAP_FALLBACK_CLAIM,
            )
        case _:
            assert_never(mode)


def _eligibility(claim: FallbackClaim) -> tuple[str, ...]:
    """Bind fallback eligibility parameters for the selected claim mode."""
    common = (claim.detected_before, claim.claimed_at, claim.claimed_at)
    match claim.mode:
        case "configured":
            return (json.dumps(list(claim.priorities)), *common)
        case "bootstrap":
            return common
        case _:
            assert_never(claim.mode)
