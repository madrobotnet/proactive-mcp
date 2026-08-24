"""Typed persistence and state machine transitions for situations."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from ._situation_consistency import (
    DetectionSourceMismatchError,
    SituationConsistencyStore,
)
from ._situation_models import (
    DeliveryClaim,
    DeliveryReservation,
    Detection,
    DetectionApplySummary,
    DetectionUpsertSummary,
    InvalidSituationTransitionError,
    Situation,
    SituationNotFoundError,
    SituationState,
    SituationType,
    SituationValidationError,
)
from ._situation_sql import (
    ACKNOWLEDGE_SITUATION,
    EXPIRE_LAPSED,
    INSERT_TYPE_MUTE,
    MARK_DELIVERED,
    MUTE_SITUATION,
    SNOOZE_SITUATION,
    WAKE_SNOOZED,
)
from ._source_generation import DelayedSourceGenerationError
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from proactive_mcp.clock import Clock

    from ._situation_reader import SituationReader
    from ._source_generation import SourceGeneration, SourceGenerationStatus
    from .sync import SourceErrorCode, SyncStore

__all__ = [
    "DelayedSourceGenerationError",
    "DetectionSourceMismatchError",
    "SituationStore",
]

MAX_SITUATION_PAGE_SIZE = 100


class SituationStore:
    """Persist situations and enforce their delivery state machine."""

    _connection: sqlite3.Connection
    _clock: Clock
    _reader: SituationReader
    _sync: SyncStore
    _consistency: SituationConsistencyStore

    def __init__(
        self,
        connection: sqlite3.Connection,
        clock: Clock,
        sync_store: SyncStore,
    ) -> None:
        """Bind situation persistence to an open connection and clock."""
        self._connection = connection
        self._clock = clock
        self._sync = sync_store
        self._consistency = SituationConsistencyStore(connection, clock, sync_store)
        self._reader = self._consistency.reader

    @property
    def reader(self) -> SituationReader:
        """Return the one reader whose capture callbacks this connection owns."""
        return self._reader

    def upsert_detections(
        self, detections: Sequence[Detection]
    ) -> DetectionUpsertSummary:
        """Persist one detection batch without duplicating a dedupe key."""
        return self._consistency.upsert_detections(detections)

    def apply_source_generation(  # noqa: PLR0913
        self,
        generation: SourceGeneration,
        detections: Sequence[Detection],
        *,
        status: SourceGenerationStatus,
        sync_cursor: str | None = None,
        error_code: SourceErrorCode | None = None,
        resolve_absent: bool = False,
        resolution_scope_ids: Collection[str] = (),
        resolution_excluded_ids: Collection[str] = (),
    ) -> DetectionApplySummary:
        """Atomically accept source truth, detections, and resolutions."""
        return self._consistency.apply_source_generation(
            generation,
            detections,
            status,
            sync_cursor=sync_cursor,
            error_code=error_code,
            resolve_absent=resolve_absent,
            resolution_scope_ids=resolution_scope_ids,
            resolution_excluded_ids=resolution_excluded_ids,
        )

    def apply_local_detections(
        self, detections: Sequence[Detection]
    ) -> DetectionApplySummary:
        """Atomically apply local personal detections and resolutions."""
        return self._consistency.apply_local_detections(detections)

    def resolve_absent(
        self,
        situation_type: SituationType,
        present_keys: Collection[str],
    ) -> int:
        """Resolve active situations absent from successful source truth."""
        return self._consistency.resolve_absent(situation_type, present_keys)

    def expire_lapsed(self) -> int:
        """Expire active situations whose relevance window has passed."""
        timestamp = self._now_iso()
        cursor = self._connection.execute(
            EXPIRE_LAPSED,
            (timestamp, timestamp, timestamp),
        )
        return cursor.rowcount

    def wake_snoozed(self) -> int:
        """Return snoozed situations whose wake time arrived to pending."""
        timestamp = self._now_iso()
        cursor = self._connection.execute(WAKE_SNOOZED, (timestamp, timestamp))
        return cursor.rowcount

    def mark_delivered(self, situation_ids: Sequence[int]) -> tuple[Situation, ...]:
        """Transition pending situations to delivered, one id at a time."""
        timestamp = self._now_iso()
        delivered: list[Situation] = []
        with ImmediateTransaction(self._connection):
            for situation_id in situation_ids:
                cursor = self._connection.execute(
                    MARK_DELIVERED,
                    (timestamp, timestamp, situation_id),
                )
                if cursor.rowcount == 0:
                    self._raise_transition_error(situation_id, "deliver")
                situation = self._require_situation(situation_id)
                self._consistency.record_delivery(situation, timestamp)
                delivered.append(situation)
        return tuple(delivered)

    def claim_for_delivery(self, claim: DeliveryClaim) -> tuple[Situation, ...]:
        """Atomically claim only rows that pass all attention limits."""
        return self._consistency.claim_for_delivery(claim)

    def reserve_for_delivery(
        self,
        claim: DeliveryClaim,
        *,
        claim_token: str,
        expires_at: datetime,
    ) -> DeliveryReservation:
        """Lease pending situations without prematurely recording delivery."""
        return self._consistency.reserve_for_delivery(
            claim,
            claim_token=claim_token,
            expires_at=_utc_iso(expires_at),
        )

    def confirm_delivery(self, claim_token: str) -> tuple[Situation, ...]:
        """Confirm that a host received one leased proactive result."""
        return self._consistency.confirm_delivery(
            claim_token,
            confirmed_at=self._now_iso(),
        )

    def acknowledge_situation(self, situation_id: int) -> Situation:
        """Mark one active situation as acknowledged by the user."""
        return self._transition(situation_id, ACKNOWLEDGE_SITUATION, "acknowledge")

    def snooze_situation(self, situation_id: int, until: datetime) -> Situation:
        """Hold one active situation until the given wake time."""
        if until.tzinfo is None:
            raise SituationValidationError(
                field="until",
                reason="must be timezone-aware",
            )
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                SNOOZE_SITUATION,
                (_utc_iso(until), timestamp, situation_id),
            )
            if cursor.rowcount == 0:
                self._raise_transition_error(situation_id, "snooze")
            return self._require_situation(situation_id)

    def mute_situation(self, situation_id: int) -> Situation:
        """Mute one active situation instance."""
        return self._transition(situation_id, MUTE_SITUATION, "mute")

    def mute_situation_type(self, situation_id: int) -> Situation:
        """Mute one delivered situation and its whole type in one transaction."""
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                MUTE_SITUATION,
                (timestamp, timestamp, situation_id),
            )
            if cursor.rowcount == 0:
                self._raise_transition_error(situation_id, "mute type")
            situation = self._require_situation(situation_id)
            _ = self._connection.execute(
                INSERT_TYPE_MUTE,
                (situation.situation_type, timestamp),
            )
            return situation

    def muted_situation_types(self) -> tuple[SituationType, ...]:
        """Return the muted situation types in stable order."""
        return self._reader.muted_types()

    def list_situations(
        self,
        state: SituationState | None = None,
        *,
        after_id: int = 0,
        limit: int = 20,
    ) -> tuple[Situation, ...]:
        """List situations, optionally filtered to one state, oldest first."""
        if after_id < 0 or not 1 <= limit <= MAX_SITUATION_PAGE_SIZE:
            raise SituationValidationError(
                field="pagination",
                reason="after_id must be nonnegative and limit must be 1..100",
            )
        return self._reader.list_situations(
            state,
            after_id=after_id,
            limit=limit,
        )

    def count_situations(self, state: SituationState | None = None) -> int:
        """Count situations without materializing their evidence."""
        return self._reader.count_situations(state)

    def count_pending_unclaimed(self, now: datetime) -> int:
        """Count rows another host has not already leased."""
        return self._reader.count_pending_unclaimed(_utc_iso(now))

    def get_situation(self, situation_id: int) -> Situation | None:
        """Return one situation by id, or None if it does not exist."""
        return self._reader.get_situation(situation_id)

    def count_delivered_between(self, start: datetime, end: datetime) -> int:
        """Count non-critical deliveries inside one half-open UTC window."""
        return self._reader.count_delivered_between(
            _utc_iso(start),
            _utc_iso(end),
        )

    def count_reserved_between(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> int:
        """Count unexpired non-critical leases in one half-open window."""
        return self._reader.count_reserved_between(
            _utc_iso(start),
            _utc_iso(end),
            _utc_iso(now),
        )

    def count_deliveries(self) -> int:
        """Return the immutable delivery-event row count."""
        return self._reader.count_deliveries()

    def _transition(self, situation_id: int, sql: str, action: str) -> Situation:
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                sql,
                (timestamp, timestamp, situation_id),
            )
            if cursor.rowcount == 0:
                self._raise_transition_error(situation_id, action)
            return self._require_situation(situation_id)

    def _raise_transition_error(self, situation_id: int, action: str) -> None:
        existing = self.get_situation(situation_id)
        if existing is None:
            raise SituationNotFoundError(situation_id)
        raise InvalidSituationTransitionError(
            id=situation_id,
            state=existing.state,
            action=action,
        )

    def _require_situation(self, situation_id: int) -> Situation:
        situation = self.get_situation(situation_id)
        if situation is None:
            raise SituationNotFoundError(situation_id)
        return situation

    def _situation_by_dedupe_key(self, dedupe_key: str) -> Situation | None:
        return self._reader.situation_by_dedupe_key(dedupe_key)

    def _now_iso(self) -> str:
        return _utc_iso(self._clock.now())


def _utc_iso(value: datetime) -> str:
    """Serialize one timezone-aware datetime as a lexicographic UTC ISO string."""
    return value.astimezone(UTC).isoformat()
