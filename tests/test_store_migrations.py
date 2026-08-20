import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

from proactive_mcp.store import DEFAULT_BUSY_TIMEOUT_MS, DatabaseStatus, Store


def _open_store_at_barrier(db_path: Path, barrier: Barrier) -> int:
    assert barrier.wait(timeout=5) >= 0
    with Store(db_path) as store:
        return store.status().migration_version


def test_temp_database_migrates_to_wal_with_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        status = store.status()

    assert isinstance(status, DatabaseStatus)
    assert status.path == db_path.resolve()
    assert status.journal_mode.lower() == "wal"
    assert status.busy_timeout == DEFAULT_BUSY_TIMEOUT_MS
    assert status.migration_version == 2


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        first = store.status()

    with Store(db_path) as store:
        second = store.status()

    assert second.migration_version == first.migration_version == 2
    assert second.journal_mode.lower() == "wal"
    assert second.busy_timeout == first.busy_timeout
    assert second.path == first.path


def test_configured_busy_timeout_is_reported(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path, busy_timeout_ms=2500) as store:
        status = store.status()

    assert status.busy_timeout == 2500
    assert status.journal_mode.lower() == "wal"
    assert status.migration_version == 2


def test_memory_schema_keeps_lead_days_nullable(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path):
        pass

    with closing(sqlite3.connect(db_path)) as connection, connection:
        _ = connection.execute(
            """
            INSERT INTO memory_items (
                kind, content, lead_days, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "note",
                "A memory without an alert lead time",
                None,
                "manual",
                "2026-08-20T00:00:00+00:00",
                "2026-08-20T00:00:00+00:00",
            ),
        )


def test_concurrent_fresh_database_startup_is_reliable(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"
    barrier = Barrier(4)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_open_store_at_barrier, db_path, barrier) for _ in range(4)
        ]

    assert [future.result(timeout=10) for future in futures] == [2, 2, 2, 2]
