from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store
from tests.store_migration_support import (
    applied_versions,
    column_defaults,
    column_names,
    foreign_keys,
    index_flags,
    scalar_int,
    table_names,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_memory_schema_keeps_lead_days_nullable(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path):
        pass

    with closing(sqlite3.connect(db_path)) as connection, connection:
        _ = connection.execute(
            """
            INSERT INTO memory_items (
                kind, content, lead_days, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "note",
                "A memory without an alert lead time",
                None,
                "manual",
                "2026-08-20T00:00:00+00:00",
                "2026-08-20T00:00:00+00:00",
            ),
        )


def test_v4_creates_entities_and_entity_aliases_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        assert store.status().migration_version == 12

    with closing(sqlite3.connect(db_path)) as connection:
        assert table_names(connection) >= {
            "entities",
            "entity_aliases",
            "memory_items",
            "schema_migrations",
            "source_sync_state",
        }
        assert column_names(connection, "entities") == {
            "id",
            "kind",
            "path",
            "label",
            "status",
            "merged_into",
            "created_at",
            "updated_at",
        }
        assert column_names(connection, "entity_aliases") == {
            "id",
            "entity_id",
            "alias",
            "alias_norm",
            "source",
            "created_at",
        }
        assert (
            scalar_int(
                connection,
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name = 'entities'
                """,
            )
            == 1
        )
        assert applied_versions(connection) == set(range(1, 13))


def test_v4_reconstructs_memory_items_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path):
        pass

    with closing(sqlite3.connect(db_path)) as connection:
        assert column_names(connection, "memory_items") == {
            "id",
            "kind",
            "entity_id",
            "attribute",
            "supersedes_id",
            "content",
            "content_norm",
            "date_anchor",
            "recurrence",
            "lead_days",
            "source",
            "created_at",
            "updated_at",
            "archived",
        }
        defaults = column_defaults(connection, "memory_items")
        assert defaults["attribute"] == "'free'"
        assert defaults["recurrence"] == "'none'"
        assert defaults["archived"] == "0"
        entity_defaults = column_defaults(connection, "entities")
        assert entity_defaults["status"] == "'active'"


def test_v4_constraints_and_indexes_are_present(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path):
        pass

    with closing(sqlite3.connect(db_path)) as connection, connection:
        entity_indexes = index_flags(connection, "entities")
        alias_indexes = index_flags(connection, "entity_aliases")
        memory_indexes = index_flags(connection, "memory_items")
        assert entity_indexes["idx_entities_kind_path"] == (0, 0)
        assert entity_indexes["idx_entities_path"] == (0, 0)
        assert alias_indexes["uq_entity_alias_norm"] == (1, 0)
        assert memory_indexes["uq_memory_dated_fact"] == (1, 1)
        assert foreign_keys(connection, "entities") == {
            ("entities", "merged_into", "id", "NO ACTION"),
        }
        assert foreign_keys(connection, "entity_aliases") == {
            ("entities", "entity_id", "id", "CASCADE"),
        }
        assert foreign_keys(connection, "memory_items") == {
            ("entities", "entity_id", "id", "NO ACTION"),
            ("memory_items", "supersedes_id", "id", "NO ACTION"),
        }

        created = "2026-08-21T00:00:00+00:00"
        _ = connection.execute(
            """
            INSERT INTO entities (kind, label, created_at, updated_at)
            VALUES ('person', 'Ada', ?, ?)
            """,
            (created, created),
        )
        entity_id = scalar_int(connection, "SELECT last_insert_rowid()")
        _ = connection.execute(
            """
            INSERT INTO entity_aliases (
                entity_id, alias, alias_norm, source, created_at
            ) VALUES (?, 'Ada', 'ada', 'manual', ?)
            """,
            (entity_id, created),
        )
        _ = connection.execute(
            """
            INSERT INTO memory_items (
                kind, entity_id, attribute, content, date_anchor, source,
                created_at, updated_at
            ) VALUES ('fact', ?, 'birthday', 'Ada birthday', '--07-18', 'manual', ?, ?)
            """,
            (entity_id, created, created),
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO entities (kind, label, created_at, updated_at)
                VALUES ('other', 'Bad', ?, ?)
                """,
                (created, created),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO entities (kind, label, status, created_at, updated_at)
                VALUES ('person', 'Bad status', 'deleted', ?, ?)
                """,
                (created, created),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO memory_items (
                    kind, content, source, created_at, updated_at
                ) VALUES ('person_fact', 'legacy kind', 'manual', ?, ?)
                """,
                (created, created),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO memory_items (
                    kind, attribute, content, source, created_at, updated_at
                ) VALUES ('fact', 'nickname', 'bad attr', 'manual', ?, ?)
                """,
                (created, created),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO entity_aliases (
                    entity_id, alias, alias_norm, source, created_at
                ) VALUES (?, 'ada', 'ada', 'manual', ?)
                """,
                (entity_id, created),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO memory_items (
                    kind, entity_id, attribute, content, date_anchor, source,
                    created_at, updated_at
                ) VALUES (
                    'fact', ?, 'birthday', 'dup birthday', '--07-18', 'manual', ?, ?
                )
                """,
                (entity_id, created, created),
            )

        _ = connection.execute(
            """
            INSERT INTO memory_items (
                kind, entity_id, attribute, content, date_anchor, source,
                created_at, updated_at
            ) VALUES (
                'fact', ?, 'birthday', 'other date', '--06-18', 'manual', ?, ?
            )
            """,
            (entity_id, created, created),
        )
        _ = connection.execute(
            """
            INSERT INTO memory_items (
                kind, entity_id, attribute, content, date_anchor, source,
                created_at, updated_at
            ) VALUES ('note', ?, 'free', 'free one', '--07-18', 'manual', ?, ?)
            """,
            (entity_id, created, created),
        )
        _ = connection.execute(
            """
            INSERT INTO memory_items (
                kind, entity_id, attribute, content, date_anchor, source,
                created_at, updated_at
            ) VALUES ('note', ?, 'free', 'free two', '--07-18', 'manual', ?, ?)
            """,
            (entity_id, created, created),
        )
