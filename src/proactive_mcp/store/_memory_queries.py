"""Typed SQLite capture helpers for memory items and entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._memory_models import (
    ENTITY_ADAPTER,
    INVALID_DATABASE_RESULT,
    MEMORY_ITEM_ADAPTER,
    Entity,
    MemoryItem,
    MemoryValidationError,
    NewMemory,
)
from ._memory_normalize import free_dated_memory_key
from ._memory_sql import (
    SELECT_DATED_DUPLICATE,
    SELECT_DATED_DUPLICATE_EXCLUDING,
    SELECT_ENTITY_BY_ID,
    SELECT_FREE_DATED_DUPLICATE,
    SELECT_FREE_DATED_DUPLICATE_EXCLUDING,
    SELECT_MEMORY_BY_ID,
)

if TYPE_CHECKING:
    import sqlite3

SqlValue = int | str | None


class MemoryQueries:
    """Read typed memory rows through SQLite UDFs."""

    _connection: sqlite3.Connection
    _items: list[MemoryItem]
    _entities: list[Entity]
    _ints: list[int]

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind capture UDFs to an open connection."""
        self._connection = connection
        self._items = []
        self._entities = []
        self._ints = []
        connection.create_function(
            "_proactive_capture_memory_item",
            1,
            self._capture_memory_item,
        )
        connection.create_function("_proactive_capture_entity", 1, self._capture_entity)
        connection.create_function(
            "_proactive_capture_memory_int",
            1,
            self._capture_int,
        )

    def execute(self, sql: str, params: tuple[SqlValue, ...] = ()) -> None:
        """Execute one SQL statement and discard the cursor."""
        _ = self._connection.execute(sql, params)

    def rollback_if_active(self) -> None:
        """Roll back the current write transaction when one remains active."""
        if self._connection.in_transaction:
            self.execute("ROLLBACK")

    def capture_items(
        self,
        sql: str,
        params: tuple[SqlValue, ...] = (),
    ) -> tuple[MemoryItem, ...]:
        """Return memory items captured by one SELECT."""
        self._items.clear()
        _ = self._connection.execute(sql, params)
        return tuple(self._items)

    def capture_entities(
        self,
        sql: str,
        params: tuple[SqlValue, ...] = (),
    ) -> tuple[Entity, ...]:
        """Return entities captured by one SELECT."""
        self._entities.clear()
        _ = self._connection.execute(sql, params)
        return tuple(self._entities)

    def query_int(self, select_sql: str, params: tuple[SqlValue, ...] = ()) -> int:
        """Return one integer column or raise a validation error."""
        value = self.query_optional_int(select_sql, params)
        if value is None:
            raise MemoryValidationError(*INVALID_DATABASE_RESULT)
        return value

    def query_optional_int(
        self,
        select_sql: str,
        params: tuple[SqlValue, ...] = (),
    ) -> int | None:
        """Return one integer column, or None when the subquery is NULL."""
        self._ints.clear()
        _ = self._connection.execute(
            f"SELECT _proactive_capture_memory_int(({select_sql}))",
            params,
        )
        return self._ints[0] if self._ints else None

    def memory_by_id(self, memory_id: int) -> MemoryItem | None:
        """Return one memory item by id, or None if it does not exist."""
        items = self.capture_items(SELECT_MEMORY_BY_ID, (memory_id,))
        return items[0] if items else None

    def entity_by_id(self, entity_id: int) -> Entity | None:
        """Return one entity by id, or None if it does not exist."""
        entities = self.capture_entities(SELECT_ENTITY_BY_ID, (entity_id,))
        return entities[0] if entities else None

    def dated_duplicate_id(
        self,
        memory: NewMemory,
        entity_id: int | None,
        *,
        excluding_id: int | None = None,
    ) -> int | None:
        """Return the id of an active identical dated memory, if one exists."""
        if memory.date_anchor is None:
            return None
        if memory.attribute == "free":
            identity = free_dated_memory_key(memory, entity_id)
            if excluding_id is None:
                return self.query_optional_int(
                    SELECT_FREE_DATED_DUPLICATE,
                    identity,
                )
            return self.query_optional_int(
                SELECT_FREE_DATED_DUPLICATE_EXCLUDING,
                (*identity, excluding_id),
            )
        if entity_id is None:
            return None
        if excluding_id is None:
            return self.query_optional_int(
                SELECT_DATED_DUPLICATE,
                (entity_id, memory.attribute, memory.date_anchor),
            )
        return self.query_optional_int(
            SELECT_DATED_DUPLICATE_EXCLUDING,
            (entity_id, memory.attribute, memory.date_anchor, excluding_id),
        )

    def _capture_memory_item(self, payload: str) -> int:
        item = MEMORY_ITEM_ADAPTER.validate_json(payload)
        self._items.append(item)
        return item.id

    def _capture_entity(self, payload: str) -> int:
        entity = ENTITY_ADAPTER.validate_json(payload)
        self._entities.append(entity)
        return entity.id

    def _capture_int(self, value: int | None) -> int:
        if value is not None:
            self._ints.append(value)
        return 0 if value is None else value
