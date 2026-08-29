from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store
from tests.v10_migration_support import create_v9_database

if TYPE_CHECKING:
    from pathlib import Path


def test_v11_marks_legacy_daemon_history_unknown_instead_of_never_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "proactive.db"
    _ = create_v9_database(database)
    connection = sqlite3.connect(database)
    try:
        _ = connection.execute(
            """
            INSERT INTO daemon_status(
                id, state, pid, started_at, heartbeat_at, cycle_count,
                owner_token, poll_interval_seconds
            ) VALUES (1, 'stopped', 4242, ?, ?, 7, 'legacy-owner', 300)
            """,
            (
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T01:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with Store(database) as store:
        status = store.daemon.status()

    assert status.cycle_count == 7
    assert status.last_run_state == "unknown"


def test_v11_rejects_partial_or_unbounded_daemon_failure_metadata(
    tmp_path: Path,
) -> None:
    with Store(tmp_path / "proactive.db") as store:
        store.daemon.record_start(4242)
        connection = store.connection()

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                UPDATE daemon_status
                SET last_failure_phase = 'credential'
                WHERE id = 1
                """
            )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                UPDATE daemon_status
                SET last_run_state = 'failed',
                    last_failure_phase = 'unbounded-phase',
                    last_failure_code = 'unbounded-code',
                    last_failure_at = '2026-08-29T12:00:00+00:00'
                WHERE id = 1
                """
            )
