from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store
from proactive_mcp.store.migrations import load_migrations
from tests.store_migration_support import (
    applied_versions,
    column_names,
    index_flags,
    table_names,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_migration_005_is_packaged_applied_and_idempotent(tmp_path: Path) -> None:
    # Given: an empty hermetic database.
    db_path = tmp_path / "proactive.db"

    # When: Store opens the database twice.
    with Store(db_path) as store:
        first = store.status().migration_version
    with Store(db_path) as store:
        second = store.status().migration_version

    # Then: migration 005 remains packaged and is applied exactly once.
    assert 5 in tuple(number for number, _sql in load_migrations())
    assert first == second == 9
    with closing(sqlite3.connect(db_path)) as connection:
        assert applied_versions(connection) == {1, 2, 3, 4, 5, 6, 7, 8, 9}


def test_migration_005_creates_situation_state_contract(tmp_path: Path) -> None:
    # Given: a database migrated through M3.
    db_path = tmp_path / "proactive.db"
    with Store(db_path):
        pass

    # When: its SQLite contract is inspected.
    with closing(sqlite3.connect(db_path)) as connection:
        situation_columns = column_names(connection, "situations")
        indexes = index_flags(connection, "situations")

        # Then: durable state, timestamps, mutes, and lookup indexes exist.
        assert table_names(connection) >= {"situations", "situation_type_mutes"}
        assert situation_columns >= {
            "id",
            "situation_type",
            "dedupe_key",
            "priority",
            "state",
            "title",
            "why_now",
            "evidence",
            "expires_at",
            "detected_at",
            "updated_at",
            "delivered_at",
            "acknowledged_at",
            "snoozed_until",
            "resolved_at",
            "expired_at",
            "muted_at",
        }
        assert indexes["idx_situations_state"] == (0, 0)
        assert indexes["idx_situations_type_state"] == (0, 0)
        assert indexes["idx_situations_delivered_at"] == (0, 0)
        assert any(unique == 1 for unique, _partial in indexes.values())


@pytest.mark.parametrize(
    ("column", "invalid"),
    [
        ("situation_type", "unknown"),
        ("priority", "urgent"),
        ("state", "forgotten"),
    ],
)
def test_migration_005_rejects_invalid_closed_variants(
    tmp_path: Path,
    column: str,
    invalid: str,
) -> None:
    # Given: a migrated situations table with closed variant constraints.
    db_path = tmp_path / "proactive.db"
    with Store(db_path):
        pass
    values = {
        "situation_type": "reply_deadline",
        "dedupe_key": f"bad-{column}",
        "priority": "routine",
        "state": "pending",
        "title": "Fixture",
        "why_now": "Fixture",
        "evidence": "{}",
        "detected_at": "2026-08-21T12:00:00+00:00",
        "updated_at": "2026-08-21T12:00:00+00:00",
    }
    values[column] = invalid

    # When/Then: SQLite rejects every out-of-contract variant.
    with (
        closing(sqlite3.connect(db_path)) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        _ = connection.execute(
            """
            INSERT INTO situations (
                situation_type, dedupe_key, priority, state, title, why_now,
                evidence, detected_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(values.values()),
        )
