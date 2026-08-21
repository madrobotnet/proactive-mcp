from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING
from unicodedata import normalize

import pytest

from proactive_mcp.store import Store
from tests.legacy_migration_support import (
    LegacyMemoryRow,
    expected_migrated_rows,
    expected_preserved_rows,
    preserved_memory_rows,
    seed_legacy_database,
    seed_legacy_v3_database,
)
from tests.store_migration_support import (
    alias_norms,
    applied_versions,
    capture_strings,
    migrated_memory_projection,
    optional_int_column,
    scalar_int,
    text_pairs,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_v4_maps_person_fact_to_fact_and_preserves_legacy_fields(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "proactive.db"
    seed_legacy_v3_database(db_path)

    with Store(db_path) as store:
        assert store.status().migration_version == 6

    with closing(sqlite3.connect(db_path)) as connection:
        assert migrated_memory_projection(connection) == expected_migrated_rows()
        assert scalar_int(connection, "SELECT COUNT(*) FROM entities") == 2
        assert scalar_int(connection, "SELECT COUNT(*) FROM entity_aliases") == 2
        assert text_pairs(connection) == [
            ("dentist", "dentist"),
            ("mother", "mother"),
        ]
        mother_ids = optional_int_column(connection, (1, 4))
        dentist_ids = optional_int_column(connection, (2,))
        assert mother_ids[0] is not None
        assert mother_ids[0] == mother_ids[1]
        assert dentist_ids[0] is not None
        assert dentist_ids[0] != mother_ids[0]
        assert optional_int_column(connection, (3, 5)) == [None, None]


def test_v4_legacy_upgrade_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"
    seed_legacy_v3_database(db_path)

    with Store(db_path) as store:
        first = store.status().migration_version

    with Store(db_path) as store:
        second = store.status().migration_version

    with closing(sqlite3.connect(db_path)) as connection:
        assert first == second == 6
        assert applied_versions(connection) == {1, 2, 3, 4, 5, 6}
        assert scalar_int(connection, "SELECT COUNT(*) FROM memory_items") == 5
        assert scalar_int(connection, "SELECT COUNT(*) FROM entities") == 2
        assert migrated_memory_projection(connection) == expected_migrated_rows()


@pytest.mark.parametrize(
    ("first", "second", "expected_norm"),
    [
        ("Ada Lovelace", "AdaLovelace", "adalovelace"),
        ("é", "e\u0301", "é"),
        ("Straße", "STRASSE", "strasse"),
    ],
)
def test_v4_collapses_equivalent_alias_spellings(
    tmp_path: Path,
    first: str,
    second: str,
    expected_norm: str,
) -> None:
    """Given one normalized key, migration keeps one entity and one alias."""
    db_path = tmp_path / "proactive.db"
    created = "2026-08-21T00:00:00+00:00"
    rows = (
        LegacyMemoryRow(
            memory_id=1,
            kind="note",
            entity=first,
            content="first spelling",
            date_anchor=None,
            recurrence="none",
            lead_days=None,
            source="manual",
            created_at=created,
            updated_at=created,
            archived=0,
        ),
        LegacyMemoryRow(
            memory_id=2,
            kind="note",
            entity=second,
            content="second spelling",
            date_anchor=None,
            recurrence="none",
            lead_days=None,
            source="manual",
            created_at=created,
            updated_at=created,
            archived=0,
        ),
    )
    seed_legacy_database(db_path, rows)

    with Store(db_path) as store:
        assert store.status().migration_version == 6

    with closing(sqlite3.connect(db_path)) as connection:
        assert scalar_int(connection, "SELECT COUNT(*) FROM memory_items") == 2
        assert scalar_int(connection, "SELECT COUNT(*) FROM entities") == 1
        assert scalar_int(connection, "SELECT COUNT(*) FROM entity_aliases") == 1
        assert alias_norms(connection) == {expected_norm}
        assert all(
            label == normalize("NFC", label)
            for label in capture_strings(
                connection,
                "SELECT SUM(_cap_str(label)) FROM entities",
            )
        )
        assert preserved_memory_rows(connection) == expected_preserved_rows(rows)


def test_v4_preserves_every_memory_row_and_distinct_alias(tmp_path: Path) -> None:
    """Given collapsed and distinct spellings, rows and unique keys all survive."""
    db_path = tmp_path / "proactive.db"
    created = "2026-08-21T00:00:00+00:00"
    rows = (
        LegacyMemoryRow(
            1,
            "note",
            "Ada Lovelace",
            "spacing one",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            2,
            "note",
            "AdaLovelace",
            "spacing two",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            3,
            "note",
            "é",
            "nfc spelling",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            4,
            "note",
            "e\u0301",
            "nfd spelling",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            5,
            "note",
            "Straße",
            "strasse lower",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            6,
            "note",
            "STRASSE",
            "strasse upper",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            7,
            "note",
            "mother",
            "mother row",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
        LegacyMemoryRow(
            8,
            "note",
            "dentist",
            "dentist row",
            None,
            "none",
            None,
            "manual",
            created,
            created,
            0,
        ),
    )
    seed_legacy_database(db_path, rows)

    with Store(db_path) as store:
        assert store.status().migration_version == 6

    with closing(sqlite3.connect(db_path)) as connection:
        assert scalar_int(connection, "SELECT COUNT(*) FROM memory_items") == 8
        assert scalar_int(connection, "SELECT COUNT(*) FROM entities") == 5
        assert scalar_int(connection, "SELECT COUNT(*) FROM entity_aliases") == 5
        assert alias_norms(connection) == {
            "adalovelace",
            "é",
            "strasse",
            "mother",
            "dentist",
        }
        assert preserved_memory_rows(connection) == expected_preserved_rows(rows)
