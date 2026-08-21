from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from multiprocessing import get_context
from threading import Barrier as ThreadBarrier
from typing import TYPE_CHECKING

from proactive_mcp.store import DEFAULT_BUSY_TIMEOUT_MS, DatabaseStatus, Store

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as ProcessQueue
    from multiprocessing.synchronize import Barrier as ProcessBarrier
    from pathlib import Path


def _open_store_at_barrier(db_path: Path, barrier: ThreadBarrier) -> int:
    assert barrier.wait(timeout=5) >= 0
    with Store(db_path) as store:
        return store.status().migration_version


def test_temp_database_migrates_to_wal_with_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        status = store.status()

    assert isinstance(status, DatabaseStatus)
    assert status.path == db_path.absolute()
    assert status.journal_mode.lower() == "wal"
    assert status.busy_timeout == DEFAULT_BUSY_TIMEOUT_MS
    assert status.migration_version == 3


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        first = store.status()

    with Store(db_path) as store:
        second = store.status()

    assert second.migration_version == first.migration_version == 3
    assert second.journal_mode.lower() == "wal"
    assert second.busy_timeout == first.busy_timeout
    assert second.path == first.path


def test_configured_busy_timeout_is_reported(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path, busy_timeout_ms=2500) as store:
        status = store.status()

    assert status.busy_timeout == 2500
    assert status.journal_mode.lower() == "wal"
    assert status.migration_version == 3


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
    barrier = ThreadBarrier(4)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_open_store_at_barrier, db_path, barrier) for _ in range(4)
        ]

    assert [future.result(timeout=10) for future in futures] == [3, 3, 3, 3]


def _open_fresh_store_in_worker(
    db_path: Path,
    barrier: ProcessBarrier,
    versions: ProcessQueue[int],
) -> None:
    assert barrier.wait(timeout=30) >= 0
    with Store(db_path) as store:
        versions.put(store.status().migration_version)


def test_cross_process_fresh_database_startup_is_serialized(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"
    process_count = 4
    context = get_context("spawn")
    barrier = context.Barrier(process_count)
    versions: ProcessQueue[int] = context.Queue()
    processes = [
        context.Process(
            target=_open_fresh_store_in_worker,
            args=(db_path, barrier, versions),
        )
        for _ in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)

    assert [(process.is_alive(), process.exitcode) for process in processes] == [
        (False, 0)
    ] * process_count
    observed_versions = [versions.get(timeout=10) for _ in range(process_count)]

    assert observed_versions == [3] * process_count
