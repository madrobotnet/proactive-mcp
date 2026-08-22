from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Detection, SituationEvidence, Store
from proactive_mcp.store.migrations import load_migrations
from tests.store_migration_support import column_names, scalar_int, table_names

if TYPE_CHECKING:
    from pathlib import Path

_CLAIMED_AT = "2026-08-21T16:00:00+00:00"
_COMPLETED_AT = "2026-08-21T16:00:05+00:00"
_INSERT_CLAIM = """
    INSERT INTO situation_fallbacks (situation_id, priority, outcome, claimed_at)
    VALUES (?, 'critical', 'claimed', ?)
    """
_INSERT_DAEMON = """
    INSERT INTO daemon_status (id, state, pid, started_at, heartbeat_at)
    VALUES (?, 'running', 4242, ?, ?)
    """


def _claimed_fallback(store: Store) -> int:
    detection = Detection(
        "calendar_conflict",
        "fallback-key",
        "critical",
        "Fixture conflict",
        "Fixture starts soon",
        SituationEvidence(),
    )
    _ = store.situations.upsert_detections((detection,))
    situation_id = store.situations.list_situations()[0].id
    _ = store.connection().execute(_INSERT_CLAIM, (situation_id, _CLAIMED_AT))
    return situation_id


def _open_at_barrier(path: Path, barrier: Barrier) -> int:
    assert barrier.wait(timeout=30) >= 0
    with Store(path) as store:
        return store.status().migration_version


def test_migration_007_adds_daemon_and_fallback_contract(tmp_path: Path) -> None:
    # Given/When: an empty database is migrated to head.
    with Store(tmp_path / "db") as store:
        connection = store.connection()

        # Then: daemon liveness and fallback history carry only structural fields.
        assert tuple(number for number, _sql in load_migrations())[-1] == 7
        assert store.status().migration_version == 7
        assert table_names(connection) >= {"daemon_status", "situation_fallbacks"}
        assert column_names(connection, "daemon_status") == {
            "id",
            "state",
            "pid",
            "started_at",
            "heartbeat_at",
            "cycle_count",
        }
        assert column_names(connection, "situation_fallbacks") == {
            "id",
            "situation_id",
            "priority",
            "outcome",
            "failure_code",
            "claimed_at",
            "completed_at",
        }


def test_migration_007_is_idempotent_across_reopen(tmp_path: Path) -> None:
    # Given: one migrated database.
    path = tmp_path / "db"
    with Store(path) as store:
        first = store.status().migration_version

    # When: the same database is reopened.
    with Store(path) as store:
        second = store.status().migration_version
        applied = scalar_int(
            store.connection(),
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 7",
        )

    # Then: the migration is recorded exactly once.
    assert (first, second, applied) == (7, 7, 1)


def test_migration_007_applies_once_under_concurrent_startup(tmp_path: Path) -> None:
    # Given: four openers released together on a fresh database.
    path = tmp_path / "db"
    barrier = Barrier(4)

    # When: they migrate the same database concurrently.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = tuple(
            executor.submit(_open_at_barrier, path, barrier) for _ in range(4)
        )
    versions = [future.result(timeout=60) for future in futures]

    # Then: every opener observes head and version 7 was applied once.
    with Store(path) as store:
        applied = scalar_int(
            store.connection(),
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 7",
        )
    assert versions == [7, 7, 7, 7]
    assert applied == 1


def test_migration_007_fallback_history_is_one_shot(tmp_path: Path) -> None:
    # Given: one claimed fallback row for a situation.
    with Store(tmp_path / "db") as store:
        situation_id = _claimed_fallback(store)
        connection = store.connection()

        # When/Then: neither a retry row nor a deletion is possible.
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(_INSERT_CLAIM, (situation_id, _COMPLETED_AT))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("DELETE FROM situation_fallbacks")


def test_migration_007_fallback_claim_facts_are_immutable(tmp_path: Path) -> None:
    # Given: one claimed fallback row.
    with Store(tmp_path / "db") as store:
        _ = _claimed_fallback(store)
        connection = store.connection()

        # When/Then: claim identity, time, and priority cannot be rewritten.
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "UPDATE situation_fallbacks SET claimed_at = ?",
                (_COMPLETED_AT,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("UPDATE situation_fallbacks SET priority = 'high'")
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("UPDATE situation_fallbacks SET situation_id = 99")


def test_migration_007_fallback_outcome_is_written_exactly_once(
    tmp_path: Path,
) -> None:
    # Given: one claimed fallback row completed as sent.
    with Store(tmp_path / "db") as store:
        _ = _claimed_fallback(store)
        connection = store.connection()
        _ = connection.execute(
            "UPDATE situation_fallbacks SET outcome = 'sent', completed_at = ?",
            (_COMPLETED_AT,),
        )

        # When/Then: a second outcome cannot overwrite the recorded one.
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                UPDATE situation_fallbacks
                SET outcome = 'failed', failure_code = 'timeout'
                """
            )


def test_migration_007_terminal_outcome_requires_its_own_fields(tmp_path: Path) -> None:
    # Given: one claimed fallback row.
    with Store(tmp_path / "db") as store:
        situation_id = _claimed_fallback(store)
        connection = store.connection()

        # When/Then: sent needs a completion time and failed needs a code.
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("UPDATE situation_fallbacks SET outcome = 'sent'")
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "UPDATE situation_fallbacks SET outcome = 'failed', completed_at = ?",
                (_COMPLETED_AT,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO situation_fallbacks (
                    situation_id, priority, outcome, claimed_at, failure_code
                ) VALUES (?, 'critical', 'claimed', ?, 'timeout')
                """,
                (situation_id, _CLAIMED_AT),
            )


def test_migration_007_daemon_status_holds_one_enumerated_row(tmp_path: Path) -> None:
    # Given: one daemon heartbeat row.
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DAEMON, (1, _CLAIMED_AT, _CLAIMED_AT))

        # When/Then: a second row and unknown liveness states are rejected.
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(_INSERT_DAEMON, (2, _CLAIMED_AT, _CLAIMED_AT))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("UPDATE daemon_status SET state = 'paused'")
