"""Atomic detection application and delivery claims."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Final

from ._situation_claim import (
    claim_for_delivery,
    confirm_delivery,
    record_delivery,
    reserve_for_delivery,
)
from ._situation_models import (
    SITUATION_EVIDENCE_ADAPTER,
    DeliveryClaim,
    DeliveryReservation,
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
_MAX_SITUATION_ROWS: Final[int] = 10_000
_MAX_SITUATION_RECORD_BYTES: Final[int] = 16 * 1024


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

    def apply_source_generation(  # noqa: PLR0913
        self,
        generation: SourceGeneration,
        detections: Sequence[Detection],
        status: SourceGenerationStatus,
        *,
        sync_cursor: str | None = None,
        error_code: SourceErrorCode | None = None,
        resolve_absent: bool = False,
        resolution_scope_ids: Collection[str] = (),
        resolution_excluded_ids: Collection[str] = (),
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
            elif resolve_absent:
                resolved = self._resolve_excluding_source_ids(
                    expected_type,
                    {item.dedupe_key for item in detections},
                    set(resolution_excluded_ids),
                    timestamp,
                )
            elif resolution_scope_ids:
                resolved = self._resolve_in_source_ids(
                    expected_type,
                    {item.dedupe_key for item in detections},
                    set(resolution_scope_ids),
                    timestamp,
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
    ) -> tuple[Situation, ...]:
        """Consume one unexpired host receipt and record delivery."""
        return confirm_delivery(
            self._connection,
            self._reader,
            claim_token,
            confirmed_at=confirmed_at,
        )

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

    def _resolve_excluding_source_ids(
        self,
        situation_type: SituationType,
        present_keys: set[str],
        excluded_ids: set[str],
        timestamp: str,
    ) -> int:
        return self._resolve_by_source_ids(
            situation_type,
            present_keys,
            excluded_ids,
            timestamp,
            include=False,
        )

    def _resolve_in_source_ids(
        self,
        situation_type: SituationType,
        present_keys: set[str],
        included_ids: set[str],
        timestamp: str,
    ) -> int:
        return self._resolve_by_source_ids(
            situation_type,
            present_keys,
            included_ids,
            timestamp,
            include=True,
        )

    def _resolve_by_source_ids(
        self,
        situation_type: SituationType,
        present_keys: set[str],
        source_ids: set[str],
        timestamp: str,
        *,
        include: bool,
    ) -> int:
        resolved = 0
        for situation in self._reader.active_by_type(situation_type):
            source_id = situation.evidence.facts.get("thread_id")
            if situation.dedupe_key in present_keys or source_id is None:
                continue
            if (source_id in source_ids) != include:
                continue
            cursor = self._connection.execute(
                RESOLVE_SITUATION,
                (timestamp, timestamp, situation.id),
            )
            resolved += cursor.rowcount
        return resolved

    def _upsert_detection(self, detection: Detection, timestamp: str) -> str:
        evidence_bytes = SITUATION_EVIDENCE_ADAPTER.dump_json(detection.evidence)
        record_size = len(evidence_bytes) + sum(
            len(value.encode("utf-8"))
            for value in (
                detection.situation_type,
                detection.dedupe_key,
                detection.priority,
                detection.title,
                detection.why_now,
            )
        )
        existing = self._reader.situation_by_dedupe_key(detection.dedupe_key)
        if record_size > _MAX_SITUATION_RECORD_BYTES or (
            existing is None and self._reader.count_situations() >= _MAX_SITUATION_ROWS
        ):
            return "skipped"
        expires_at = (
            _utc_iso(detection.expires_at) if detection.expires_at is not None else None
        )
        evidence = evidence_bytes.decode()
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
