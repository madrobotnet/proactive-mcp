"""Shared v9 database setup for v10 migration regressions."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from proactive_mcp.store.migrations import load_migrations
from tests.store_migration_support import capture_ints

if TYPE_CHECKING:
    from pathlib import Path


def _identity(value: str) -> str:
    return value


def create_v9_database(path: Path) -> tuple[int, ...]:
    """Create a migrated v9 database with one preserved source state."""
    connection = sqlite3.connect(path)
    connection.create_function(
        "_proactive_alias_norm", 1, _identity, deterministic=True
    )
    connection.create_function(
        "_proactive_normalize_label", 1, _identity, deterministic=True
    )
    try:
        for version, sql in load_migrations():
            if version > 9:
                break
            _ = connection.executescript(sql)
            _ = connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
        _ = connection.execute(
            """
            INSERT INTO source_sync_state (
                source, auth_state, last_success_at, last_attempt_at,
                last_error_code, sync_cursor, updated_at
            ) VALUES ('gmail', 'configured', ?, ?, NULL, ?, ?)
            """,
            (
                "2020-08-25T12:00:00+00:00",
                "2020-08-25T12:00:00+00:00",
                "opaque-cursor",
                "2020-08-25T12:00:00+00:00",
            ),
        )
        connection.commit()
        versions_sql = (
            "SELECT SUM(_cap_int(version)) FROM schema_migrations ORDER BY version"
        )
        return tuple(capture_ints(connection, versions_sql))
    finally:
        connection.close()
