from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Detection, SituationEvidence, Store
from proactive_mcp.store.migrations import load_migrations
from tests.store_migration_support import column_names, table_names

if TYPE_CHECKING:
    from pathlib import Path


def test_migration_006_adds_generation_and_delivery_contract(tmp_path: Path) -> None:
    # Given/When: an empty database is migrated.
    path = tmp_path / "db"
    with Store(path) as store:
        connection = store.connection()
        version = store.status().migration_version
        # Then: generation state and delivery history exist.
        assert 6 in tuple(number for number, _sql in load_migrations())
        assert version == 9
        assert table_names(connection) >= {
            "source_detection_generations",
            "situation_deliveries",
        }
        assert column_names(connection, "source_detection_generations") >= {
            "source",
            "issued_generation",
            "applied_generation",
            "status",
        }
        assert column_names(connection, "situation_deliveries") >= {
            "situation_id",
            "delivered_at",
            "priority",
        }


def test_migration_006_delivery_history_is_immutable(tmp_path: Path) -> None:
    # Given: one delivery recorded by the compatibility API.
    with Store(tmp_path / "db") as store:
        item = Detection(
            "reply_deadline",
            "immutable",
            "routine",
            "title",
            "why",
            SituationEvidence(),
        )
        _ = store.situations.upsert_detections((item,))
        _ = store.situations.mark_delivered((store.situations.list_situations()[0].id,))
        # When/Then: history cannot be rewritten or removed.
        with pytest.raises(sqlite3.IntegrityError):
            _ = store.connection().execute(
                "UPDATE situation_deliveries SET priority = 'critical'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = store.connection().execute("DELETE FROM situation_deliveries")
