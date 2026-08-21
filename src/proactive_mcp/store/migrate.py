"""SQLite schema migration transaction helpers."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from .migrations import load_migrations


class ScalarIntReader(Protocol):
    """Read integer scalars needed by migration bookkeeping."""

    def query_int(
        self,
        select_sql: str,
        params: tuple[str, ...] = (),
    ) -> int:
        """Return one integer scalar."""
        ...


def apply_migrations(
    connection: sqlite3.Connection,
    reader: ScalarIntReader,
) -> None:
    """Apply every pending migration inside one immediate transaction."""
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        current = current_version(reader)
        for version, sql in load_migrations():
            if version <= current:
                continue
            for statement in _sql_statements(sql):
                _ = connection.execute(statement)
            _ = connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            current = version
        _ = connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            _ = connection.execute("ROLLBACK")
        raise


def current_version(reader: ScalarIntReader) -> int:
    """Return the latest applied migration version."""
    table_count = reader.query_int(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
        ("table", "schema_migrations"),
    )
    if table_count == 0:
        return 0
    return reader.query_int("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")


def _sql_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    for part in sql.split(";"):
        statement = part.strip()
        if statement:
            statements.append(statement)
    return tuple(statements)
