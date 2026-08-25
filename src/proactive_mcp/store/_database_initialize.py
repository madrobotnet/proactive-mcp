"""SQLite connection initialization shared by the Store facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ._memory_normalize import normalize_alias, normalize_label
from .migrate import apply_migrations
from .storage_errors import ReceiptErasurePendingError

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
    _complete_pending_receipt_erasure(connection, reader)


def _complete_pending_receipt_erasure(
    connection: sqlite3.Connection,
    reader: ScalarReader,
) -> None:
    """Checkpoint deleted v9 receipts before exposing the migrated store."""
    pending = reader.query_int(
        """
        SELECT COUNT(*) FROM migration_maintenance
        WHERE task = 'v9_receipt_erasure' AND pending = 1
        """
    )
    if pending == 0:
        return

    cursor = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result = cast("tuple[int, int, int] | None", cursor.fetchone())
    cursor.close()
    if result is None:
        raise ReceiptErasurePendingError
    busy, log_frames, checkpointed_frames = result
    if busy != 0 or log_frames != 0 or checkpointed_frames != 0:
        raise ReceiptErasurePendingError

    connection.execute(
        "DELETE FROM migration_maintenance WHERE task = 'v9_receipt_erasure'"
    ).close()
