"""Atomic detection application and delivery claims."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ._situation_claim import (
    claim_for_delivery,
    confirm_delivery,
    record_delivery,
    reserve_for_delivery,
)
from ._situation_models import (
    DeliveryClaim,
    DeliveryConfirmation,
    DeliveryReservation,
    Detection,
    DetectionApplySummary,
    DetectionUpsertSummary,
    Situation,
    SituationType,
)
from ._situation_reader import SituationReader
from ._situation_time import utc_iso
from ._situation_upsert import SituationUpserter
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Collection, Sequence

    from proactive_mcp.clock import Clock

    from ._source_generation import (
        SourceGeneration,
        SourceGenerationStatus,
        SourceName,
    )
    from .sync import SourceErrorCode, SourceReadDiagnostics, SyncStore

_SOURCE_TYPES: Final[dict[SourceName, SituationType]] = {
    "gmail": "reply_deadline",
    "calendar": "calendar_conflict",
}
_RESOLVES_ABSENT: Final[dict[SourceGenerationStatus, bool]] = {
    "complete": True,
    "degraded": False,
}


class SituationConsistencyStore:
    """Own atomic truth application and delivery reservation."""

    _connection: sqlite3.Connection
    _clock: Clock
    _reader: SituationReader
    _sync: SyncStore
    _upserter: SituationUpserter

    def __init__(
        self,
        connection: sqlite3.Connection,
        clock: Clock,
        sync_store: SyncStore,
    ) -> None:
        """Bind consistency operations to existing store collaborators."""
        self._connection = connection
        self._clock = clock
        self._reader = SituationReader(connection)
        self._sync = sync_store
        self._upserter = SituationUpserter(connection, self._reader)

    @property
    def reader(self) -> SituationReader:
        """Return the reader whose SQLite callbacks this store owns."""
        return self._reader

    def upsert_detections(
        self, detections: Sequence[Detection]
    ) -> DetectionUpsertSummary:
        """Persist one compatibility detection batch atomically."""
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            return self._upserter.upsert_batch(
                detections,
                timestamp,
                None,
                "local",
                None,
            )

    def apply_source_generation(  # noqa: PLR0913
        self,
        generation: SourceGeneration,
        detections: Sequence[Detection],
        status: SourceGenerationStatus,
        *,
        sync_cursor: str | None = None,
        error_code: SourceErrorCode | None = None,
        diagnostics: SourceReadDiagnostics | None = None,
        resolve_absent: bool = False,
        resolution_scope_ids: Collection[str] = (),
        resolution_excluded_ids: Collection[str] = (),
    ) -> DetectionApplySummary:
        """Atomically accept source truth, detections, and allowed resolutions."""
        expected_type = _SOURCE_TYPES[generation.source]
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            self._sync.accept_source_generation(generation, status, diagnostics)
            summary = self._upserter.upsert_batch(
                detections,
                timestamp,
                expected_type,
                generation.source,
                generation.number,
            )
            resolved = 0
            if _RESOLVES_ABSENT[status]:
                resolved = self._upserter.resolve(
                    expected_type,
                    {item.dedupe_key for item in detections},
                    timestamp,
                    generation.source,
                )
            elif resolve_absent:
                resolved = self._upserter.resolve_by_source_ids(
                    expected_type,
                    {item.dedupe_key for item in detections},
                    set(resolution_excluded_ids),
                    timestamp,
                    include=False,
                    source_name=generation.source,
                )
            elif resolution_scope_ids:
                resolved = self._upserter.resolve_by_source_ids(
                    expected_type,
                    {item.dedupe_key for item in detections},
                    set(resolution_scope_ids),
                    timestamp,
                    include=True,
                    source_name=generation.source,
                )
            if error_code is None and status == "complete":
                self._sync.record_sync_success(
                    generation.source,
                    sync_cursor=sync_cursor,
                )
            elif error_code is None:
                self._sync.record_sync_failure(
                    generation.source,
                    error_code="degraded",
                )
            elif error_code == "invalid_grant":
                self._sync.record_google_invalid_grant_in_transaction()
            else:
                self._sync.record_sync_failure(
                    generation.source,
                    error_code=error_code,
                )
        return DetectionApplySummary(summary, resolved)

    def apply_local_detections(
        self, detections: Sequence[Detection]
    ) -> DetectionApplySummary:
        """Atomically apply local personal detections and resolutions."""
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            summary = self._upserter.upsert_batch(
                detections,
                timestamp,
                "personal_occasion",
                "memory",
                None,
            )
            resolved = self._upserter.resolve(
                "personal_occasion",
                {item.dedupe_key for item in detections},
                timestamp,
                "memory",
            )
        return DetectionApplySummary(summary, resolved)

    def resolve_absent(
        self,
        situation_type: SituationType,
        present_keys: Collection[str],
    ) -> int:
        """Atomically resolve active rows absent from successful source truth."""
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            return self._upserter.resolve(situation_type, set(present_keys), timestamp)

    def claim_for_delivery(self, claim: DeliveryClaim) -> tuple[Situation, ...]:
        """Atomically claim only rows that pass all attention limits."""
        return claim_for_delivery(self._connection, self._reader, claim)

    def reserve_for_delivery(
        self,
        claim: DeliveryClaim,
        *,
        claim_token: str,
        expires_at: str,
    ) -> DeliveryReservation:
        """Lease pending rows until the host confirms receiving the result."""
        return reserve_for_delivery(
            self._connection,
            self._reader,
            claim,
            claim_token=claim_token,
            expires_at=expires_at,
        )

    def confirm_delivery(
        self,
        claim_token: str,
        *,
        confirmed_at: str,
    ) -> DeliveryConfirmation:
        """Confirm one unexpired host receipt or replay its result."""
        return confirm_delivery(
            self._connection,
            self._reader,
            claim_token,
            confirmed_at=confirmed_at,
        )

    def record_delivery(self, situation: Situation, timestamp: str) -> None:
        """Append immutable claim-time priority history."""
        record_delivery(self._connection, situation, timestamp)

    def _now_iso(self) -> str:
        return utc_iso(self._clock.now())
