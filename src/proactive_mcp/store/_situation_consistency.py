"""Atomic detection application and delivery claims."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Final

from ._situation_claim import claim_for_delivery, record_delivery
from ._situation_models import (
    SITUATION_EVIDENCE_ADAPTER,
    DeliveryClaim,
    Detection,
    DetectionApplySummary,
    DetectionUpsertSummary,
    Situation,
    SituationType,
)
from ._situation_reader import SituationReader
from ._situation_sql import (
    INSERT_SITUATION,
    REACTIVATE_SITUATION,
    REFRESH_SITUATION,
    RESOLVE_SITUATION,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from proactive_mcp.clock import Clock

    from ._source_generation import (
        SourceGeneration,
        SourceGenerationStatus,
        SourceName,
    )
    from .sync import SourceErrorCode, SyncStore

_SOURCE_TYPES: Final[dict[SourceName, SituationType]] = {
    "gmail": "reply_deadline",
    "calendar": "calendar_conflict",
}
_RESOLVES_ABSENT: Final[dict[SourceGenerationStatus, bool]] = {
    "complete": True,
    "degraded": False,
}


class DetectionSourceMismatchError(Exception):
    """Raised when a source generation contains another source type."""

    def __init__(self, source: str, situation_type: str) -> None:
        """Initialize a boundary-safe mismatch description."""
        super().__init__(
            f"source {source!r} cannot apply situation type {situation_type!r}"
        )


class SituationConsistencyStore:
    """Own atomic truth application and delivery reservation."""

    _connection: sqlite3.Connection
    _clock: Clock
    _reader: SituationReader
    _sync: SyncStore

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
            return self._upsert_batch(detections, timestamp, None)

    def apply_source_generation(
        self,
        generation: SourceGeneration,
        detections: Sequence[Detection],
        status: SourceGenerationStatus,
        *,
        sync_cursor: str | None = None,
        error_code: SourceErrorCode | None = None,
    ) -> DetectionApplySummary:
        """Atomically accept source truth, detections, and allowed resolutions."""
        expected_type = _SOURCE_TYPES[generation.source]
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            self._sync.accept_source_generation(generation, status)
            summary = self._upsert_batch(detections, timestamp, expected_type)
            resolved = 0
            if _RESOLVES_ABSENT[status]:
                resolved = self._resolve(
                    expected_type,
                    {item.dedupe_key for item in detections},
                    timestamp,
                )
            if error_code is None:
                self._sync.record_sync_success(
                    generation.source,
                    sync_cursor=sync_cursor,
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
            summary = self._upsert_batch(detections, timestamp, "personal_occasion")
            resolved = self._resolve(
                "personal_occasion",
                {item.dedupe_key for item in detections},
                timestamp,
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
            return self._resolve(situation_type, set(present_keys), timestamp)

    def claim_for_delivery(self, claim: DeliveryClaim) -> tuple[Situation, ...]:
        """Atomically claim only rows that pass all attention limits."""
        return claim_for_delivery(self._connection, self._reader, claim)

    def record_delivery(self, situation: Situation, timestamp: str) -> None:
        """Append immutable claim-time priority history."""
        record_delivery(self._connection, situation, timestamp)

    def _upsert_batch(
        self,
        detections: Sequence[Detection],
        timestamp: str,
        expected_type: SituationType | None,
    ) -> DetectionUpsertSummary:
        created = reactivated = refreshed = skipped = 0
        for detection in detections:
            if expected_type is not None and detection.situation_type != expected_type:
                raise DetectionSourceMismatchError(
                    expected_type, detection.situation_type
                )
            outcome = self._upsert_detection(detection, timestamp)
            created += outcome == "created"
            reactivated += outcome == "reactivated"
            refreshed += outcome == "refreshed"
            skipped += outcome == "skipped"
        return DetectionUpsertSummary(created, reactivated, refreshed, skipped)

    def _resolve(
        self,
        situation_type: SituationType,
        present_keys: set[str],
        timestamp: str,
    ) -> int:
        resolved = 0
        for situation in self._reader.active_by_type(situation_type):
            if situation.dedupe_key in present_keys:
                continue
            cursor = self._connection.execute(
                RESOLVE_SITUATION, (timestamp, timestamp, situation.id)
            )
            resolved += cursor.rowcount
        return resolved

    def _upsert_detection(self, detection: Detection, timestamp: str) -> str:
        existing = self._reader.situation_by_dedupe_key(detection.dedupe_key)
        expires_at = (
            _utc_iso(detection.expires_at) if detection.expires_at is not None else None
        )
        evidence = SITUATION_EVIDENCE_ADAPTER.dump_json(detection.evidence).decode()
        if existing is None:
            _ = self._connection.execute(
                INSERT_SITUATION,
                (
                    detection.situation_type,
                    detection.dedupe_key,
                    detection.priority,
                    detection.title,
                    detection.why_now,
                    evidence,
                    expires_at,
                    timestamp,
                    timestamp,
                ),
            )
            return "created"
        params = (
            detection.priority,
            detection.title,
            detection.why_now,
            evidence,
            expires_at,
        )
        if existing.state in {"resolved", "expired"}:
            if (
                existing.situation_type == "personal_occasion"
                and self._reader.has_delivery(existing.id)
            ):
                return "skipped"
            _ = self._connection.execute(
                REACTIVATE_SITUATION,
                (*params, timestamp, timestamp, existing.id),
            )
            return "reactivated"
        if existing.state in {"pending", "delivered", "snoozed"}:
            _ = self._connection.execute(
                REFRESH_SITUATION, (*params, timestamp, existing.id)
            )
            return "refreshed"
        return "skipped"

    def _now_iso(self) -> str:
        return _utc_iso(self._clock.now())


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
