"""Shared v9 database setup for v10 migration regressions."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from proactive_mcp.store.migrations import load_migrations
from tests.store_migration_support import capture_ints, scalar_int

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
            ) VALUES ('gmail', 'authorized', ?, ?, NULL, ?, ?)
            """,
            (
                "2026-08-25T12:00:00+00:00",
                "2026-08-25T12:00:00+00:00",
                "opaque-cursor",
                "2026-08-25T12:00:00+00:00",
            ),
        )
        connection.commit()
        versions_sql = (
            "SELECT SUM(_cap_int(version)) FROM schema_migrations ORDER BY version"
        )
        return tuple(capture_ints(connection, versions_sql))
    finally:
        connection.close()


def insert_v9_claim(path: Path, receipt: str) -> None:
    """Insert the original active v9 delivery-claim fixture."""
    connection = sqlite3.connect(path)
    try:
        _ = connection.execute(
            """
            INSERT INTO situations (
                situation_type, dedupe_key, state, priority, title, why_now,
                evidence, detected_at, updated_at
            ) VALUES (
                'reply_deadline', 'v9-active-claim', 'pending', 'routine',
                'fixture', 'fixture', '{}', ?, ?
            )
            """,
            ("2026-08-25T12:00:00+00:00", "2026-08-25T12:00:00+00:00"),
        )
        situation_id = scalar_int(
            connection,
            "SELECT id FROM situations WHERE dedupe_key = 'v9-active-claim'",
        )
        _ = connection.execute(
            """
            INSERT INTO situation_delivery_claims (
                claim_token, situation_id, claimed_at, expires_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                receipt,
                situation_id,
                "2026-08-25T12:00:00+00:00",
                "2026-08-25T12:02:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()
