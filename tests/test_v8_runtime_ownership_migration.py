from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import TYPE_CHECKING

from proactive_mcp.store import Store
from proactive_mcp.store.migrations import load_migrations
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_POLL_INTERVAL = timedelta(minutes=5)


def _identity(value: str) -> str:
    return value


def _write_v7_running_daemon(
    path: Path,
    *,
    heartbeat_at: datetime,
) -> None:
    """Create the persisted running row that migration 008 upgrades."""
    with sqlite3.connect(path) as connection:
        connection.create_function("_proactive_normalize_label", 1, _identity)
        connection.create_function("_proactive_alias_norm", 1, _identity)
        for version, sql in load_migrations():
            if version > 7:
                break
            _ = connection.executescript(sql)
            _ = connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)",
                (version,),
            )
        timestamp = heartbeat_at.isoformat()
        _ = connection.execute(
            """
            INSERT INTO daemon_status (
                id, state, pid, started_at, heartbeat_at, cycle_count
            ) VALUES (1, 'running', 4242, ?, ?, 3)
            """,
            (timestamp, timestamp),
        )


def test_migrated_stale_daemon_row_can_be_claimed_by_the_new_runtime(
    tmp_path: Path,
) -> None:
    # Given: v7 persisted a running row before ownership tokens or cadence existed.
    started = utc_datetime(2026, 8, 21, 16)
    database = tmp_path / "db"
    _write_v7_running_daemon(database, heartbeat_at=started)
    clock = FakeClock(started + 3 * _POLL_INTERVAL + timedelta(seconds=1))

    # When: the upgraded daemon starts after the fallback cadence is stale.
    with Store(database, clock=clock) as store:
        claimed = store.daemon.try_record_start(
            pid=7777,
            poll_interval=_POLL_INTERVAL,
        )
        status = store.daemon.status()

    # Then: the legacy null token cannot permanently fence out the new runtime.
    assert claimed is True
    assert status.liveness == "running"
    assert status.pid == 7777
    assert status.cycle_count == 0
    assert status.poll_interval == _POLL_INTERVAL
