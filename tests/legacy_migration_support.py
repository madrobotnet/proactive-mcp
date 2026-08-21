"""Legacy v3 seed data and preservation helpers for store migration tests."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter
from tests.store_migration_support import MigratedMemoryTuple, capture_json_rows

from proactive_mcp.store.migrations import load_migrations

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class LegacyMemoryRow:
    memory_id: int
    kind: str
    entity: str | None
    content: str
    date_anchor: str | None
    recurrence: str
    lead_days: int | None
    source: str
    created_at: str
    updated_at: str
    archived: int


@dataclass(frozen=True, slots=True)
class _PreservedMemoryRow:
    memory_id: int
    kind: str
    content: str
    date_anchor: str | None
    recurrence: str
    lead_days: int | None
    source: str
    created_at: str
    updated_at: str
    archived: int


_PRESERVED_ROW_ADAPTER: Final[TypeAdapter[_PreservedMemoryRow]] = TypeAdapter(
    _PreservedMemoryRow
)

_LEGACY_V3_ROWS: tuple[LegacyMemoryRow, ...] = (
    LegacyMemoryRow(
        memory_id=1,
        kind="person_fact",
        entity="mother",
        content="Mom's birthday",
        date_anchor="--07-18",
        recurrence="yearly",
        lead_days=7,
        source="agent_conversation",
        created_at="2026-07-11T09:00:00+00:00",
        updated_at="2026-07-11T09:00:00+00:00",
        archived=0,
    ),
    LegacyMemoryRow(
        memory_id=2,
        kind="commitment",
        entity="dentist",
        content="Call the dentist",
        date_anchor="2026-09-01",
        recurrence="none",
        lead_days=3,
        source="manual",
        created_at="2026-07-12T10:00:00+00:00",
        updated_at="2026-07-12T10:00:00+00:00",
        archived=0,
    ),
    LegacyMemoryRow(
        memory_id=3,
        kind="preference",
        entity=None,
        content="Prefers tea",
        date_anchor=None,
        recurrence="none",
        lead_days=None,
        source="manual",
        created_at="2026-07-13T11:00:00+00:00",
        updated_at="2026-07-13T11:00:00+00:00",
        archived=0,
    ),
    LegacyMemoryRow(
        memory_id=4,
        kind="person_fact",
        entity="mother",
        content="Birthday in June",
        date_anchor="--06-18",
        recurrence="yearly",
        lead_days=7,
        source="agent_conversation",
        created_at="2026-07-14T12:00:00+00:00",
        updated_at="2026-07-14T12:00:00+00:00",
        archived=0,
    ),
    LegacyMemoryRow(
        memory_id=5,
        kind="note",
        entity=None,
        content="old note",
        date_anchor=None,
        recurrence="none",
        lead_days=None,
        source="agent_conversation",
        created_at="2026-07-15T13:00:00+00:00",
        updated_at="2026-07-16T14:00:00+00:00",
        archived=1,
    ),
)


def seed_legacy_v3_database(db_path: Path) -> None:
    seed_legacy_database(db_path, _LEGACY_V3_ROWS)


def seed_legacy_database(
    db_path: Path,
    rows: tuple[LegacyMemoryRow, ...],
) -> None:
    with closing(sqlite3.connect(db_path)) as connection, connection:
        for version, sql in load_migrations():
            if version > 3:
                continue
            for part in sql.split(";"):
                statement = part.strip()
                if statement:
                    _ = connection.execute(statement)
            _ = connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
        for row in rows:
            _ = connection.execute(
                """
                INSERT INTO memory_items (
                    id, kind, entity, content, date_anchor, recurrence, lead_days,
                    source, created_at, updated_at, archived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.memory_id,
                    row.kind,
                    row.entity,
                    row.content,
                    row.date_anchor,
                    row.recurrence,
                    row.lead_days,
                    row.source,
                    row.created_at,
                    row.updated_at,
                    row.archived,
                ),
            )


def expected_migrated_rows() -> list[MigratedMemoryTuple]:
    expected: list[MigratedMemoryTuple] = []
    for row in _LEGACY_V3_ROWS:
        kind = "fact" if row.kind == "person_fact" else row.kind
        expected.append(
            (
                row.memory_id,
                kind,
                row.entity,
                row.content,
                row.date_anchor,
                row.recurrence,
                row.lead_days,
                row.source,
                row.created_at,
                row.updated_at,
                row.archived,
                "free",
                None,
            )
        )
    return expected


def preserved_memory_rows(
    connection: sqlite3.Connection,
) -> list[_PreservedMemoryRow]:
    return capture_json_rows(
        connection,
        _PRESERVED_ROW_ADAPTER,
        """
        SELECT SUM(_cap_json(json_object(
            'memory_id', id,
            'kind', kind,
            'content', content,
            'date_anchor', date_anchor,
            'recurrence', recurrence,
            'lead_days', lead_days,
            'source', source,
            'created_at', created_at,
            'updated_at', updated_at,
            'archived', archived
        )))
        FROM memory_items
        ORDER BY id
        """,
    )


def expected_preserved_rows(
    rows: tuple[LegacyMemoryRow, ...],
) -> list[_PreservedMemoryRow]:
    return [
        _PreservedMemoryRow(
            memory_id=row.memory_id,
            kind="fact" if row.kind == "person_fact" else row.kind,
            content=row.content,
            date_anchor=row.date_anchor,
            recurrence=row.recurrence,
            lead_days=row.lead_days,
            source=row.source,
            created_at=row.created_at,
            updated_at=row.updated_at,
            archived=row.archived,
        )
        for row in rows
    ]
