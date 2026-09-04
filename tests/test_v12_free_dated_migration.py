from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from proactive_mcp.store import Store
from tests.store_migration_support import column_names, index_flags, scalar_int
from tests.v10_migration_support import create_v9_database

if TYPE_CHECKING:
    from pathlib import Path

_OLDER = "2026-08-20T09:00:00+00:00"
_NEWER = "2026-08-21T09:00:00+00:00"


def _insert_legacy_rows(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        """
        INSERT INTO entities (kind, label, created_at, updated_at)
        VALUES ('person', 'Mom', ?, ?)
        """,
        (_OLDER, _OLDER),
    )
    entity_id = scalar_int(connection, "SELECT last_insert_rowid()")
    rows = (
        (
            "note",
            entity_id,
            "  Café  Friday  ",
            "2026-08-28",
            "none",
            7,
            "manual",
            _OLDER,
            _OLDER,
        ),
        (
            "note",
            entity_id,
            "cafe\u0301 friday",
            "2026-08-28",
            "none",
            3,
            "agent_conversation",
            _NEWER,
            _NEWER,
        ),
        (
            "fact",
            None,
            "  Tax   Day ",
            "2026-08-29",
            "yearly",
            7,
            "manual",
            _OLDER,
            _OLDER,
        ),
        (
            "fact",
            None,
            "tax day",
            "2026-08-29",
            "yearly",
            1,
            "agent_conversation",
            _NEWER,
            _NEWER,
        ),
        (
            "note",
            None,
            "Review Day",
            "2026-08-30",
            "none",
            7,
            "manual",
            _OLDER,
            _OLDER,
        ),
        (
            "fact",
            None,
            "Review Day",
            "2026-08-30",
            "none",
            7,
            "manual",
            _OLDER,
            _OLDER,
        ),
    )
    _ = connection.executemany(
        """
        INSERT INTO memory_items (
            kind, entity_id, attribute, content, date_anchor, recurrence,
            lead_days, source, created_at, updated_at
        ) VALUES (?, ?, 'free', ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def test_migration_archives_legacy_free_dated_duplicates(tmp_path: Path) -> None:
    database = tmp_path / "proactive.db"
    _ = create_v9_database(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        _insert_legacy_rows(connection)

    with Store(database) as store:
        assert store.status().migration_version == 12

    with closing(sqlite3.connect(database)) as connection:
        assert "content_norm" in column_names(connection, "memory_items")
        assert index_flags(connection, "memory_items")["uq_memory_free_dated"] == (
            1,
            1,
        )
        entity_rows = connection.execute(
            """
            SELECT id, content, updated_at, archived
            FROM memory_items
            WHERE entity_id IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
        entity_free_rows = connection.execute(
            """
            SELECT id, content, updated_at, archived
            FROM memory_items
            WHERE entity_id IS NULL AND date_anchor = '2026-08-29'
            ORDER BY id
            """
        ).fetchall()
        distinct_kind_rows = connection.execute(
            """
            SELECT kind, archived
            FROM memory_items
            WHERE entity_id IS NULL AND date_anchor = '2026-08-30'
            ORDER BY id
            """
        ).fetchall()
        normalized = connection.execute(
            """
            SELECT content_norm
            FROM memory_items
            ORDER BY id
            """
        ).fetchall()

    assert entity_rows == [
        (1, "  Café  Friday  ", _NEWER, 0),
        (2, "cafe\u0301 friday", _NEWER, 1),
    ]
    assert entity_free_rows == [
        (3, "  Tax   Day ", _NEWER, 0),
        (4, "tax day", _NEWER, 1),
    ]
    assert distinct_kind_rows == [("note", 0), ("fact", 0)]
    assert normalized == [
        ("café friday",),
        ("café friday",),
        ("tax day",),
        ("tax day",),
        ("review day",),
        ("review day",),
    ]
