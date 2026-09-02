"""Detection upsert and source-resolution persistence."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Final, Literal

from ._situation_models import (
    SITUATION_EVIDENCE_ADAPTER,
    DetectionUpsertSummary,
)
from ._situation_sql import (
    INSERT_SITUATION,
    REACTIVATE_SITUATION,
    REFRESH_SITUATION,
    RESOLVE_SITUATION,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from ._situation_models import (
        Detection,
        SituationSource,
        SituationType,
    )
    from ._situation_reader import SituationReader

_MAX_SITUATION_ROWS: Final = 10_000
_MAX_SITUATION_RECORD_BYTES: Final = 16 * 1024
_UpsertOutcome = Literal[
    "created", "reactivated", "refreshed", "skipped", "capacity_skipped"
]


class DetectionSourceMismatchError(Exception):
    """Raised when a source generation contains another source type."""

    def __init__(self, source: str, situation_type: str) -> None:
        super().__init__(
            f"source {source!r} cannot apply situation type {situation_type!r}"
        )


class SituationUpserter:
    """Apply bounded detection records and source-scoped resolutions."""

    _connection: sqlite3.Connection
    _reader: SituationReader

    def __init__(self, connection: sqlite3.Connection, reader: SituationReader) -> None:
        self._connection = connection
        self._reader = reader

    def upsert_batch(
        self,
        detections: Sequence[Detection],
        timestamp: str,
        expected_type: SituationType | None,
        source_name: SituationSource,
        source_generation: int | None,
    ) -> DetectionUpsertSummary:
        created = reactivated = refreshed = skipped = capacity_skipped = 0
        for detection in detections:
            if expected_type is not None and detection.situation_type != expected_type:
                raise DetectionSourceMismatchError(
                    expected_type, detection.situation_type
                )
            outcome = self._upsert_detection(
                detection,
                timestamp,
                source_name,
                source_generation,
            )
            created += outcome == "created"
            reactivated += outcome == "reactivated"
            refreshed += outcome == "refreshed"
            skipped += outcome in {"skipped", "capacity_skipped"}
            capacity_skipped += outcome == "capacity_skipped"
        return DetectionUpsertSummary(
            created, reactivated, refreshed, skipped, capacity_skipped
        )

    def resolve(
        self,
        situation_type: SituationType,
        present_keys: set[str],
        timestamp: str,
        source_name: SituationSource | None = None,
    ) -> int:
        resolved = 0
        for situation in self._reader.active_by_type(situation_type, source_name):
            if situation.dedupe_key in present_keys:
                continue
            cursor = self._connection.execute(
                RESOLVE_SITUATION, (timestamp, timestamp, situation.id)
            )
            resolved += cursor.rowcount
        return resolved

    def resolve_by_source_ids(  # noqa: PLR0913
        self,
        situation_type: SituationType,
        present_keys: set[str],
        source_ids: set[str],
        timestamp: str,
        *,
        include: bool,
        source_name: SituationSource | None = None,
    ) -> int:
        resolved = 0
        for situation in self._reader.active_by_type(situation_type, source_name):
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

    def _upsert_detection(
        self,
        detection: Detection,
        timestamp: str,
        source_name: SituationSource,
        source_generation: int | None,
    ) -> _UpsertOutcome:
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
            return "capacity_skipped"
        if detection.expires_at is None:
            expires_at = None
        else:
            expires_at = detection.expires_at.astimezone(UTC).isoformat()
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
                    source_name,
                    source_generation,
                ),
            )
            return "created"
        params = (
            detection.priority,
            detection.title,
            detection.why_now,
            evidence,
            expires_at,
            source_name,
            source_generation,
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
