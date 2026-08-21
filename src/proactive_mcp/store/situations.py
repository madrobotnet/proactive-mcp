"""Typed persistence and state machine transitions for situations."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from ._situation_models import (
    SITUATION_EVIDENCE_ADAPTER,
    Detection,
    DetectionUpsertSummary,
    InvalidSituationTransitionError,
    Situation,
    SituationNotFoundError,
    SituationState,
    SituationType,
    SituationValidationError,
)
from ._situation_reader import SituationReader
from ._situation_sql import (
    ACKNOWLEDGE_SITUATION,
    EXPIRE_LAPSED,
    INSERT_SITUATION,
    INSERT_TYPE_MUTE,
    MARK_DELIVERED,
    MUTE_SITUATION,
    REACTIVATE_SITUATION,
    REFRESH_SITUATION,
    RESOLVE_SITUATION,
    SNOOZE_SITUATION,
    WAKE_SNOOZED,
)
from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from proactive_mcp.clock import Clock

__all__ = ["SituationStore"]


class SituationStore:
    """Persist situations and enforce their delivery state machine."""

    _connection: sqlite3.Connection
    _clock: Clock
    _reader: SituationReader

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind situation persistence to an open connection and clock."""
        self._connection = connection
        self._clock = clock
        self._reader = SituationReader(connection)

    def upsert_detections(
        self,
        detections: Sequence[Detection],
    ) -> DetectionUpsertSummary:
        """Persist one detection batch without ever duplicating a dedupe key.

        A new key inserts a pending situation. A key whose situation was
        resolved or expired reactivates it as pending with a fresh
        ``detected_at``. A key in an active state only refreshes priority,
        title, why-now, evidence, and expiry. Acknowledged and muted
        situations are left untouched.
        """
        created = reactivated = refreshed = skipped = 0
        timestamp = self._now_iso()
        with ImmediateTransaction(self._connection):
            for detection in detections:
                outcome = self._upsert_detection(detection, timestamp)
                created += 1 if outcome == "created" else 0
                reactivated += 1 if outcome == "reactivated" else 0
                refreshed += 1 if outcome == "refreshed" else 0
                skipped += 1 if outcome == "skipped" else 0
        return DetectionUpsertSummary(
            created=created,
            reactivated=reactivated,
            refreshed=refreshed,
            skipped=skipped,
        )

    def resolve_absent(
        self,
        situation_type: SituationType,
        present_keys: Collection[str],
    ) -> int:
        """Resolve active situations of one type whose keys are no longer detected.

        Only call this with detections from a fresh, successful source read.
        Resolving from a stale or failed source would violate the
        no-all-clear-when-stale invariant.
        """
        timestamp = self._now_iso()
        keys = set(present_keys)
        resolved = 0
        with ImmediateTransaction(self._connection):
            for situation in self._reader.active_by_type(situation_type):
                if situation.dedupe_key in keys:
                    continue
                _ = self._connection.execute(
                    RESOLVE_SITUATION,
                    (timestamp, timestamp, situation.id),
                )
                resolved += 1
        return resolved

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
                delivered.append(self._require_situation(situation_id))
        return tuple(delivered)

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

    def mute_situation_type(self, situation_type: SituationType) -> None:
        """Mute every current and future situation of one type."""
        _ = self._connection.execute(
            INSERT_TYPE_MUTE,
            (situation_type, self._now_iso()),
        )

    def muted_situation_types(self) -> tuple[SituationType, ...]:
        """Return the muted situation types in stable order."""
        return self._reader.muted_types()

    def list_situations(
        self,
        state: SituationState | None = None,
    ) -> tuple[Situation, ...]:
        """List situations, optionally filtered to one state, oldest first."""
        return self._reader.list_situations(state)

    def get_situation(self, situation_id: int) -> Situation | None:
        """Return one situation by id, or None if it does not exist."""
        return self._reader.get_situation(situation_id)

    def count_delivered_between(self, start: datetime, end: datetime) -> int:
        """Count non-critical deliveries inside one half-open UTC window."""
        return self._reader.count_delivered_between(
            _utc_iso(start),
            _utc_iso(end),
        )

    def _upsert_detection(self, detection: Detection, timestamp: str) -> str:
        existing = self._situation_by_dedupe_key(detection.dedupe_key)
        expires_at = (
            _utc_iso(detection.expires_at) if detection.expires_at is not None else None
        )
        evidence = SITUATION_EVIDENCE_ADAPTER.dump_json(detection.evidence).decode(
            "utf-8"
        )
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
        refresh_params = (
            detection.priority,
            detection.title,
            detection.why_now,
            evidence,
            expires_at,
        )
        if existing.state in {"resolved", "expired"}:
            _ = self._connection.execute(
                REACTIVATE_SITUATION,
                (*refresh_params, timestamp, timestamp, existing.id),
            )
            return "reactivated"
        if existing.state in {"pending", "delivered", "snoozed"}:
            _ = self._connection.execute(
                REFRESH_SITUATION,
                (*refresh_params, timestamp, existing.id),
            )
            return "refreshed"
        return "skipped"

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
