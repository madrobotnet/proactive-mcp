"""Typed persistence operations for memory items."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal

from pydantic import TypeAdapter

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

MemoryKind = Literal["person_fact", "commitment", "preference", "note"]
MemoryRecurrence = Literal["none", "yearly"]
MemorySource = Literal["agent_conversation", "manual"]


@dataclass(frozen=True, slots=True)
class NewMemory:
    """A typed memory item ready to persist."""

    kind: MemoryKind
    content: str
    entity: str | None = None
    date_anchor: str | None = None
    recurrence: MemoryRecurrence = "none"
    lead_days: int = 7
    source: MemorySource = "agent_conversation"


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """A stored memory item."""

    id: int
    kind: MemoryKind
    entity: str | None
    content: str
    date_anchor: str | None
    recurrence: MemoryRecurrence
    lead_days: int | None
    source: MemorySource
    created_at: str
    updated_at: str
    archived: bool


_MEMORY_ITEM_ADAPTER: Final[TypeAdapter[MemoryItem]] = TypeAdapter(MemoryItem)


@dataclass(frozen=True, slots=True)
class MemoryNotFoundError(Exception):
    """Raised when a memory item does not exist."""

    id: int

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"memory item {self.id} not found")


class MemoryStore:
    """Persist and retrieve memory items through a SQLite connection."""

    _connection: sqlite3.Connection
    _clock: Clock
    _items: list[MemoryItem]
    _ints: list[int]

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Bind persistence operations to an open connection and clock."""
        self._connection = connection
        self._clock = clock
        self._items = []
        self._ints = []
        connection.create_function(
            "_proactive_capture_memory_item",
            1,
            self._capture_memory_item,
        )
        connection.create_function(
            "_proactive_capture_memory_int",
            1,
            self._capture_int,
        )

    def remember(self, memory: NewMemory) -> MemoryItem:
        """Insert a memory item without replacing contradictory items."""
        timestamp = self._clock.now().isoformat()
        _ = self._connection.execute(
            """
            INSERT INTO memory_items (
                kind, entity, content, date_anchor, recurrence, lead_days, source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.kind,
                memory.entity,
                memory.content,
                memory.date_anchor,
                memory.recurrence,
                memory.lead_days,
                memory.source,
                timestamp,
                timestamp,
            ),
        )
        return MemoryItem(
            id=self._query_int("SELECT last_insert_rowid()"),
            kind=memory.kind,
            entity=memory.entity,
            content=memory.content,
            date_anchor=memory.date_anchor,
            recurrence=memory.recurrence,
            lead_days=memory.lead_days,
            source=memory.source,
            created_at=timestamp,
            updated_at=timestamp,
            archived=False,
        )

    def recall(
        self,
        query: str,
        *,
        kind: MemoryKind | None = None,
    ) -> tuple[MemoryItem, ...]:
        """Return active memory items matching a literal entity or content substring."""
        self._items.clear()
        pattern = f"%{_escape_like(query)}%"
        if kind is not None:
            sql = """
                SELECT SUM(_proactive_capture_memory_item(
                    json_object(
                        'id', id,
                        'kind', kind,
                        'entity', entity,
                        'content', content,
                        'date_anchor', date_anchor,
                        'recurrence', recurrence,
                        'lead_days', lead_days,
                        'source', source,
                        'created_at', created_at,
                        'updated_at', updated_at,
                        'archived', archived
                    )
                ))
                FROM (
                    SELECT * FROM memory_items
                    WHERE archived = 0
                      AND (entity LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
                      AND kind = ?
                    ORDER BY id ASC
                )
            """
            params = (pattern, pattern, kind)
        else:
            sql = """
                SELECT SUM(_proactive_capture_memory_item(
                    json_object(
                        'id', id,
                        'kind', kind,
                        'entity', entity,
                        'content', content,
                        'date_anchor', date_anchor,
                        'recurrence', recurrence,
                        'lead_days', lead_days,
                        'source', source,
                        'created_at', created_at,
                        'updated_at', updated_at,
                        'archived', archived
                    )
                ))
                FROM (
                    SELECT * FROM memory_items
                    WHERE archived = 0
                      AND (entity LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
                    ORDER BY id ASC
                )
            """
            params = (pattern, pattern)
        _ = self._connection.execute(sql, params)
        return tuple(self._items)

    def forget(self, memory_id: int) -> MemoryItem:
        """Soft-archive a memory item, leaving an archived item unchanged."""
        item = self._memory_by_id(memory_id)
        if item is None:
            raise MemoryNotFoundError(memory_id)
        if item.archived:
            return item
        timestamp = self._clock.now().isoformat()
        _ = self._connection.execute(
            """
            UPDATE memory_items
            SET archived = 1, updated_at = ?
            WHERE id = ? AND archived = 0
            """,
            (timestamp, memory_id),
        )
        return replace(item, updated_at=timestamp, archived=True)

    def _memory_by_id(self, memory_id: int) -> MemoryItem | None:
        self._items.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(_proactive_capture_memory_item(
                json_object(
                    'id', id,
                    'kind', kind,
                    'entity', entity,
                    'content', content,
                    'date_anchor', date_anchor,
                    'recurrence', recurrence,
                    'lead_days', lead_days,
                    'source', source,
                    'created_at', created_at,
                    'updated_at', updated_at,
                    'archived', archived
                )
            ))
            FROM (
                SELECT * FROM memory_items WHERE id = ?
            )
            """,
            (memory_id,),
        )
        if not self._items:
            return None
        return self._items[0]

    def _query_int(
        self,
        select_sql: str,
        params: tuple[int, ...] = (),
    ) -> int:
        self._ints.clear()
        _ = self._connection.execute(
            f"SELECT _proactive_capture_memory_int(({select_sql}))",
            params,
        )
        return self._ints[0]

    def _capture_memory_item(self, payload: str) -> int:
        item = _MEMORY_ITEM_ADAPTER.validate_json(payload)
        self._items.append(item)
        return item.id

    def _capture_int(self, value: int) -> int:
        self._ints.append(value)
        return value


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
