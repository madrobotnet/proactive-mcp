"""Opening one private SQLite database and binding every store to it."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._database_initialize import initialize_connection
from ._database_support import ScalarReader
from ._evaluation_gate import EvaluationGate
from .daemon_status import DaemonStatusStore
from .fallbacks import FallbackStore
from .memory import MemoryStore
from .private_path import (
    UnsafeDatabasePathError,
    enforce_private_sidecars,
    open_private_parent,
    prepare_private_database_file,
    private_database_guard,
    private_initialization_lock,
    sqlite_connection_target,
)
from .situations import SituationStore
from .storage_errors import ReceiptErasurePendingError
from .sync import SyncStore

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from proactive_mcp.clock import Clock

__all__ = ["StoreCollaborators", "close_connection", "open_collaborators"]


@dataclass(frozen=True, slots=True)
class StoreCollaborators:
    """Every store sharing one migrated connection, and what it owns."""

    connection: sqlite3.Connection
    directory_fd: int | None
    reader: ScalarReader
    memory: MemoryStore
    sync: SyncStore
    situations: SituationStore
    daemon: DaemonStatusStore
    fallbacks: FallbackStore
    evaluation_gate: EvaluationGate
    database_guard: AbstractContextManager[None]


def open_collaborators(
    path: Path,
    busy_timeout_ms: int,
    clock: Clock,
) -> StoreCollaborators:
    """Open and migrate one private database, then bind its stores.

    A failed open releases the connection and directory descriptor it took,
    so the caller never owns a partially built store.
    """
    directory_fd = open_private_parent(path)
    connection: sqlite3.Connection | None = None
    database_guard = private_database_guard(directory_fd, path)
    database_guard_entered = False
    try:
        prepare_private_database_file(directory_fd, path)
        _ = database_guard.__enter__()
        database_guard_entered = True
        with private_initialization_lock(directory_fd, path):
            connection = sqlite3.connect(
                sqlite_connection_target(directory_fd, path),
                timeout=busy_timeout_ms / 1000,
            )
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms:d}").close()
            reader = ScalarReader(connection)
            initialize_connection(connection, reader)
            enforce_private_sidecars(directory_fd, path)
    except (
        OSError,
        sqlite3.Error,
        ReceiptErasurePendingError,
        UnsafeDatabasePathError,
    ):
        close_connection(
            connection,
            directory_fd,
            database_guard if database_guard_entered else None,
        )
        raise
    sync = SyncStore(connection, clock)
    situations = SituationStore(connection, clock, sync)
    return StoreCollaborators(
        connection=connection,
        directory_fd=directory_fd,
        reader=reader,
        memory=MemoryStore(connection, clock),
        sync=sync,
        situations=situations,
        daemon=DaemonStatusStore(connection, clock),
        fallbacks=FallbackStore(connection, clock, situations.reader),
        evaluation_gate=EvaluationGate(connection, clock),
        database_guard=database_guard,
    )


def close_connection(
    connection: sqlite3.Connection | None,
    directory_fd: int | None,
    database_guard: AbstractContextManager[None] | None = None,
) -> None:
    """Release one SQLite connection and its private directory descriptor."""
    if connection is not None:
        connection.close()
    if database_guard is not None:
        _ = database_guard.__exit__(None, None, None)
    if directory_fd is not None:
        os.close(directory_fd)
