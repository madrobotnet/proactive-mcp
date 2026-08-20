from pathlib import Path

from proactive_mcp.store import DEFAULT_BUSY_TIMEOUT_MS, DatabaseStatus, Store


def test_temp_database_migrates_to_wal_with_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        status = store.status()

    assert isinstance(status, DatabaseStatus)
    assert status.path == db_path.resolve()
    assert status.journal_mode.lower() == "wal"
    assert status.busy_timeout == DEFAULT_BUSY_TIMEOUT_MS
    assert status.migration_version == 1


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path) as store:
        first = store.status()

    with Store(db_path) as store:
        second = store.status()

    assert second.migration_version == first.migration_version == 1
    assert second.journal_mode.lower() == "wal"
    assert second.busy_timeout == first.busy_timeout
    assert second.path == first.path


def test_configured_busy_timeout_is_reported(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"

    with Store(db_path, busy_timeout_ms=2500) as store:
        status = store.status()

    assert status.busy_timeout == 2500
    assert status.journal_mode.lower() == "wal"
    assert status.migration_version == 1
