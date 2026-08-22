"""SQLite connection initialization shared by the Store facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._memory_normalize import normalize_alias, normalize_label
from .migrate import apply_migrations

if TYPE_CHECKING:
    import sqlite3

    from ._database_support import ScalarReader


def initialize_connection(
    connection: sqlite3.Connection,
    reader: ScalarReader,
) -> None:
    """Configure one connection and apply all packaged migrations."""
    connection.isolation_level = None
    connection.execute("PRAGMA foreign_keys = ON").close()
    connection.execute("PRAGMA journal_mode = WAL").close()
    connection.create_function(
        "_proactive_alias_norm",
        1,
        normalize_alias,
        deterministic=True,
    )
    connection.create_function(
        "_proactive_normalize_label",
        1,
        normalize_label,
        deterministic=True,
    )
    apply_migrations(connection, reader)
