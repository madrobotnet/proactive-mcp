"""Typed persistence operations for memory items and their entities."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING

from ._memory_entities import list_active_entities, resolve_entity
from ._memory_models import (
    INVALID_DUPLICATE_DATE as _INVALID_DUPLICATE_DATE,
)
from ._memory_models import (
    INVALID_LIMIT as _INVALID_LIMIT,
)
from ._memory_models import (
    Entity,
    EntityAliasConflictError,
    EntityKind,
    EntityStatus,
    MemoryAttribute,
    MemoryItem,
    MemoryKind,
    MemoryNotFoundError,
    MemoryRecurrence,
    MemorySource,
    MemoryValidationError,
    NewMemory,
)
from ._memory_normalize import escape_like, prefix_filter
from ._memory_queries import MemoryQueries
from ._memory_sql import (
    ARCHIVE_MEMORY_ITEM,
    INSERT_MEMORY_ITEM,
    LAST_INSERT_ROWID,
    SELECT_RECALL_MEMORY_ITEMS,
    UPDATE_MEMORY_ITEM,
    UPDATE_MEMORY_TIMESTAMP,
)

if TYPE_CHECKING:
    from types import TracebackType

    from proactive_mcp.clock import Clock

__all__ = [
    "Entity",
    "EntityAliasConflictError",
    "EntityKind",
    "EntityStatus",
    "MemoryAttribute",
    "MemoryItem",
    "MemoryKind",
    "MemoryNotFoundError",
    "MemoryRecurrence",
    "MemorySource",
    "MemoryStore",
    "MemoryValidationError",
    "NewMemory",
]


class _WriteTransaction:
    """Serialize one multi-statement write and roll back on any failure."""

    _queries: MemoryQueries

    def __init__(self, queries: MemoryQueries) -> None:
        self._queries = queries

    def __enter__(self) -> None:
        self._queries.execute("BEGIN IMMEDIATE")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._queries.rollback_if_active()
            return
        try:
            self._queries.execute("COMMIT")
        except sqlite3.Error:
            self._queries.rollback_if_active()
            raise


class MemoryStore:
    """Persist and retrieve memory items through a SQLite connection."""

    _queries: MemoryQueries
    _clock: Clock

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind persistence operations to an open connection and clock."""
        self._queries = MemoryQueries(connection)
        self._clock = clock

    def remember(self, memory: NewMemory) -> MemoryItem:
        """Store a memory, merging an identical active dated fact."""
        timestamp = self._clock.now().isoformat()
        with _WriteTransaction(self._queries):
            entity = resolve_entity(self._queries, memory, timestamp)
            duplicate_id = self._queries.dated_duplicate_id(
                memory,
                entity.id if entity else None,
            )
            if duplicate_id is not None:
                self._queries.execute(
                    UPDATE_MEMORY_TIMESTAMP,
                    (timestamp, duplicate_id),
                )
                duplicate = self._queries.memory_by_id(duplicate_id)
                if duplicate is None:
                    raise MemoryNotFoundError(duplicate_id)
                return duplicate

            self._queries.execute(
                INSERT_MEMORY_ITEM,
                (
                    memory.kind,
                    entity.id if entity is not None else None,
                    memory.attribute,
                    memory.content,
                    memory.date_anchor,
                    memory.recurrence,
                    memory.lead_days,
                    memory.source,
                    timestamp,
                    timestamp,
                ),
            )
            memory_id = self._queries.query_int(LAST_INSERT_ROWID)
            item = self._queries.memory_by_id(memory_id)
            if item is None:
                raise MemoryNotFoundError(memory_id)
            return item

    def recall(
        self,
        query: str,
        *,
        kind: MemoryKind | None = None,
        entity_kind: EntityKind | None = None,
        path_prefix: str | None = None,
        limit: int = 20,
    ) -> tuple[MemoryItem, ...]:
        """Return active literal matches, newest first, across memory kinds."""
        if limit < 1:
            raise MemoryValidationError(*_INVALID_LIMIT)
        normalized_prefix, prefix_pattern = prefix_filter(path_prefix)
        pattern = f"%{escape_like(query)}%"
        return self._queries.capture_items(
            SELECT_RECALL_MEMORY_ITEMS,
            (
                pattern,
                pattern,
                pattern,
                pattern,
                kind,
                kind,
                entity_kind,
                entity_kind,
                normalized_prefix,
                normalized_prefix,
                prefix_pattern,
                limit,
            ),
        )

    def update(self, memory_id: int, memory: NewMemory) -> MemoryItem:
        """Replace one memory item's mutable values while retaining its identity."""
        timestamp = self._clock.now().isoformat()
        with _WriteTransaction(self._queries):
            existing = self._queries.memory_by_id(memory_id)
            if existing is None:
                raise MemoryNotFoundError(memory_id)
            entity = resolve_entity(self._queries, memory, timestamp)
            duplicate_id = self._queries.dated_duplicate_id(
                memory,
                entity.id if entity is not None else None,
                excluding_id=memory_id,
            )
            if duplicate_id is not None:
                raise MemoryValidationError(*_INVALID_DUPLICATE_DATE)
            self._queries.execute(
                UPDATE_MEMORY_ITEM,
                (
                    memory.kind,
                    entity.id if entity is not None else None,
                    memory.attribute,
                    memory.content,
                    memory.date_anchor,
                    memory.recurrence,
                    memory.lead_days,
                    memory.source,
                    timestamp,
                    existing.id,
                ),
            )
            updated = self._queries.memory_by_id(memory_id)
            if updated is None:
                raise MemoryNotFoundError(memory_id)
            return updated

    def list_entities(
        self,
        *,
        kind: EntityKind | None = None,
        path_prefix: str | None = None,
    ) -> tuple[Entity, ...]:
        """List active entities in stable kind, path, label, and id order."""
        return list_active_entities(
            self._queries,
            kind=kind,
            path_prefix=path_prefix,
        )

    def forget(self, memory_id: int) -> MemoryItem:
        """Soft-archive a memory item, leaving an archived item unchanged."""
        item = self._queries.memory_by_id(memory_id)
        if item is None:
            raise MemoryNotFoundError(memory_id)
        if item.archived:
            return item
        timestamp = self._clock.now().isoformat()
        self._queries.execute(
            ARCHIVE_MEMORY_ITEM,
            (timestamp, memory_id),
        )
        return replace(item, updated_at=timestamp, archived=True)
