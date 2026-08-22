"""Ordered source detection generations persisted in SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import TypeAdapter

SourceName = Literal["gmail", "calendar"]
SourceGenerationStatus = Literal["complete", "degraded"]


@dataclass(frozen=True, slots=True)
class SourceGeneration:
    """A source generation reserved before asynchronous work."""

    source: SourceName
    number: int


@dataclass(frozen=True, slots=True)
class SourceGenerationState:
    """Latest generation issued and durably accepted for one source."""

    source: SourceName
    issued: int
    applied: int
    status: SourceGenerationStatus | None


@dataclass(frozen=True, slots=True)
class DelayedSourceGenerationError(Exception):
    """Raised when an older source result arrives after a newer result."""

    generation: SourceGeneration
    applied: int

    def __post_init__(self) -> None:
        """Initialize a boundary-safe delayed-generation description."""
        message = (
            f"source {self.generation.source} generation {self.generation.number} "
            f"follows applied generation {self.applied}"
        )
        Exception.__init__(self, message)


@dataclass(frozen=True, slots=True)
class UnreservedSourceGenerationError(Exception):
    """Raised when a result names a generation that was never issued."""

    generation: SourceGeneration

    def __post_init__(self) -> None:
        """Initialize a boundary-safe unreserved-generation description."""
        message = f"source generation was not reserved: {self.generation}"
        Exception.__init__(self, message)


_ADAPTER: Final[TypeAdapter[SourceGenerationState]] = TypeAdapter(SourceGenerationState)


class SourceGenerationStore:
    """Issue and accept monotonically ordered source generations."""

    _connection: sqlite3.Connection

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind generation ordering to one open connection."""
        self._connection = connection
        self._states: list[SourceGenerationState] = []
        connection.create_function(
            "_proactive_capture_source_generation_state",
            1,
            self._capture_state,
        )

    def reserve(self, source: SourceName) -> SourceGeneration:
        """Atomically issue the next generation for one source."""
        _ = self._connection.execute("BEGIN IMMEDIATE")
        try:
            _ = self._connection.execute(
                """
                INSERT INTO source_detection_generations (source, issued_generation)
                VALUES (?, 1)
                ON CONFLICT(source) DO UPDATE SET
                    issued_generation = issued_generation + 1
                """,
                (source,),
            )
            state = self.state(source)
            _ = self._connection.execute("COMMIT")
        except sqlite3.Error:
            if self._connection.in_transaction:
                _ = self._connection.execute("ROLLBACK")
            raise
        return SourceGeneration(source=source, number=state.issued)

    def state(self, source: SourceName) -> SourceGenerationState:
        """Return generation progress, defaulting to zero before first issue."""
        self._states.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(_proactive_capture_source_generation_state(json_object(
                'source', source, 'issued', issued_generation,
                'applied', applied_generation, 'status', status
            )))
            FROM source_detection_generations WHERE source = ?
            """,
            (source,),
        )
        if self._states:
            return self._states[0]
        return SourceGenerationState(source=source, issued=0, applied=0, status=None)

    def accept(
        self,
        generation: SourceGeneration,
        status: SourceGenerationStatus,
    ) -> None:
        """Accept one reserved generation inside the caller transaction."""
        cursor = self._connection.execute(
            """
            UPDATE source_detection_generations
            SET applied_generation = ?, status = ?
            WHERE source = ? AND issued_generation >= ?
              AND applied_generation < ?
            """,
            (
                generation.number,
                status,
                generation.source,
                generation.number,
                generation.number,
            ),
        )
        if cursor.rowcount != 0:
            return
        state = self.state(generation.source)
        if generation.number <= state.applied:
            raise DelayedSourceGenerationError(generation, state.applied)
        raise UnreservedSourceGenerationError(generation)

    def _capture_state(self, payload: str) -> int:
        self._states.append(_ADAPTER.validate_json(payload))
        return 1
