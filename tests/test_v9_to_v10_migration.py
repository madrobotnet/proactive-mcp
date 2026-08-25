from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import TYPE_CHECKING, Final, TypeAlias, cast, get_args

import pytest
from pydantic import TypeAdapter

from proactive_mcp.store import Store
from proactive_mcp.store.migrate import apply_migrations
from proactive_mcp.store.migrations import load_migrations
from proactive_mcp.store.sync import SourceReadOutcome, SourceReadReason
from tests.store_migration_support import (
    capture_ints,
    capture_json_rows,
    column_names,
    scalar_int,
    table_names,
)

if TYPE_CHECKING:
    from pathlib import Path

LegacyRow: TypeAlias = tuple[
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
]
_LEGACY_ROW_ADAPTER: Final[TypeAdapter[LegacyRow]] = TypeAdapter(LegacyRow)

_OUTCOMES: Final = cast("tuple[str, ...]", get_args(SourceReadOutcome))
_REASONS: Final = cast("tuple[str, ...]", get_args(SourceReadReason))
_INSERT_DIAGNOSTICS: Final = """
    INSERT INTO gmail_diagnostics (
        id, outcome, request_count, page_count, projected_count,
        excluded_count, byte_budget
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """


def _identity(value: str) -> str:
    return value


class _ScalarReader:
    _connection: sqlite3.Connection

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._values: list[int] = []
        connection.create_function("_migration_test_int", 1, self._capture)

    def query_int(
        self,
        select_sql: str,
        params: tuple[str, ...] = (),
    ) -> int:
        self._values.clear()
        _ = self._connection.execute(
            f"SELECT _migration_test_int(({select_sql}))", params
        )
        assert self._values
        return self._values[0]

    def _capture(self, value: int) -> int:
        self._values.append(value)
        return value


def _create_v9_database(path: Path) -> tuple[int, ...]:
    connection = sqlite3.connect(path)
    connection.create_function(
        "_proactive_alias_norm", 1, _identity, deterministic=True
    )
    connection.create_function(
        "_proactive_normalize_label", 1, _identity, deterministic=True
    )
    try:
        for version, sql in load_migrations():
            if version > 9:
                break
            _ = connection.executescript(sql)
            _ = connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
        _ = connection.execute(
            """
            INSERT INTO source_sync_state (
                source, auth_state, last_success_at, last_attempt_at,
                last_error_code, sync_cursor, updated_at
            ) VALUES ('gmail', 'authorized', ?, ?, NULL, ?, ?)
            """,
            (
                "2026-08-25T12:00:00+00:00",
                "2026-08-25T12:00:00+00:00",
                "opaque-cursor",
                "2026-08-25T12:00:00+00:00",
            ),
        )
        connection.commit()
        versions_sql = (
            "SELECT SUM(_cap_int(version)) FROM schema_migrations ORDER BY version"
        )
        return tuple(capture_ints(connection, versions_sql))
    finally:
        connection.close()


def _legacy_row(connection: sqlite3.Connection) -> LegacyRow:
    rows = capture_json_rows(
        connection,
        _LEGACY_ROW_ADAPTER,
        """
        SELECT SUM(_cap_json(json_array(
            source, auth_state, last_success_at, last_attempt_at,
            last_error_code, sync_cursor, updated_at
        )))
        FROM source_sync_state WHERE source = 'gmail'
        """,
    )
    assert len(rows) == 1
    return rows[0]


def _open_at_barrier(
    path: Path,
    ready: Event,
    barrier: Barrier,
) -> tuple[int, LegacyRow]:
    ready.set()
    assert barrier.wait(timeout=10) >= 0
    with Store(path) as store:
        return store.status().migration_version, _legacy_row(store.connection())


def test_fresh_and_v9_databases_reach_v10_without_losing_legacy_rows(
    tmp_path: Path,
) -> None:
    fresh_path = tmp_path / "fresh.db"
    legacy_path = tmp_path / "legacy.db"
    assert _create_v9_database(legacy_path) == tuple(range(1, 10))

    before_connection = sqlite3.connect(legacy_path)
    try:
        before = _legacy_row(before_connection)
    finally:
        before_connection.close()

    with Store(fresh_path) as fresh, Store(legacy_path) as upgraded:
        assert fresh.status().migration_version == 10
        assert upgraded.status().migration_version == 10
        assert _legacy_row(upgraded.connection()) == before
        assert table_names(fresh.connection()) >= {
            "gmail_diagnostics",
            "gmail_diagnostic_reason_counts",
            "confirmed_delivery_receipts",
        }


def test_two_concurrent_v9_opens_apply_migration_once(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _ = _create_v9_database(path)
    barrier = Barrier(3)
    ready = (Event(), Event())
    executor = ThreadPoolExecutor(max_workers=2)
    futures = tuple(
        executor.submit(_open_at_barrier, path, signal, barrier) for signal in ready
    )
    try:
        assert all(signal.wait(timeout=10) for signal in ready)
        assert barrier.wait(timeout=10) >= 0
        results = [future.result(timeout=30) for future in futures]
    finally:
        for future in futures:
            _ = future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    assert [version for version, _row in results] == [10, 10]
    assert results[0][1] == results[1][1]
    with Store(path) as store:
        assert (
            scalar_int(
                store.connection(),
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 10",
            )
            == 1
        )


def test_migration_10_rolls_back_all_changes_on_statement_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    _ = _create_v9_database(path)
    setup_connection = sqlite3.connect(path)
    try:
        before = _legacy_row(setup_connection)
        _ = setup_connection.execute(
            "CREATE TABLE gmail_diagnostic_reason_counts (id INTEGER)"
        )
        setup_connection.commit()
    finally:
        setup_connection.close()

    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="already exists"):
            apply_migrations(connection, _ScalarReader(connection))
        assert _legacy_row(connection) == before
        assert "gmail_diagnostics" not in table_names(connection)
        assert "confirmed_delivery_receipts" not in table_names(connection)
        assert (
            scalar_int(
                connection,
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 10",
            )
            == 0
        )
    finally:
        connection.close()


def test_gmail_diagnostics_is_a_constrained_singleton(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DIAGNOSTICS, (1, "healthy", 0, 0, 0, 0, 0))

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(_INSERT_DIAGNOSTICS, (2, "healthy", 0, 0, 0, 0, 0))


@pytest.mark.parametrize("outcome", _OUTCOMES)
def test_gmail_diagnostics_accepts_only_closed_outcomes(
    tmp_path: Path,
    outcome: str,
) -> None:
    with Store(tmp_path / f"{outcome}.db") as store:
        _ = store.connection().execute(_INSERT_DIAGNOSTICS, (1, outcome, 0, 0, 0, 0, 0))

    with Store(tmp_path / "invalid.db") as store, pytest.raises(sqlite3.IntegrityError):
        _ = store.connection().execute(
            _INSERT_DIAGNOSTICS, (1, "email@example.com", 0, 0, 0, 0, 0)
        )


@pytest.mark.parametrize(
    "update_sql",
    [
        "UPDATE gmail_diagnostics SET request_count = -1",
        "UPDATE gmail_diagnostics SET page_count = -1",
        "UPDATE gmail_diagnostics SET projected_count = -1",
        "UPDATE gmail_diagnostics SET excluded_count = -1",
        "UPDATE gmail_diagnostics SET byte_budget = -1",
    ],
)
def test_gmail_diagnostic_counters_reject_negative_values(
    tmp_path: Path,
    update_sql: str,
) -> None:
    with Store(tmp_path / "negative.db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DIAGNOSTICS, (1, "partial", 0, 0, 0, 0, 0))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(update_sql)


@pytest.mark.parametrize("reason", _REASONS)
def test_reason_counts_accept_the_complete_closed_reason_set(
    tmp_path: Path,
    reason: str,
) -> None:
    with Store(tmp_path / f"{reason}.db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DIAGNOSTICS, (1, "partial", 0, 0, 0, 0, 0))
        _ = connection.execute(
            """
            INSERT INTO gmail_diagnostic_reason_counts (diagnostic_id, reason, count)
            VALUES (1, ?, 0)
            """,
            (reason,),
        )


def test_reason_counts_reject_unknown_reasons_and_negative_counts(
    tmp_path: Path,
) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DIAGNOSTICS, (1, "partial", 0, 0, 0, 0, 0))
        for reason, count in (("sender@example.com", 1), ("stale", -1)):
            with pytest.raises(sqlite3.IntegrityError):
                _ = connection.execute(
                    """
                    INSERT INTO gmail_diagnostic_reason_counts
                        (diagnostic_id, reason, count)
                    VALUES (1, ?, ?)
                    """,
                    (reason, count),
                )


def test_reason_counts_require_the_singleton_diagnostic_parent(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO gmail_diagnostic_reason_counts
                    (diagnostic_id, reason, count)
                VALUES (1, 'stale', 1)
                """
            )


@pytest.mark.parametrize(
    ("receipt_token", "confirmed_at"),
    [("", "2026-08-25T12:00:00+00:00"), ("opaque-token", "")],
)
def test_confirmed_receipts_reject_empty_structural_fields(
    tmp_path: Path,
    receipt_token: str,
    confirmed_at: str,
) -> None:
    with Store(tmp_path / "db") as store, pytest.raises(sqlite3.IntegrityError):
        _ = store.connection().execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, 1, ?)",
            (receipt_token, confirmed_at),
        )


def test_new_tables_have_only_bounded_structural_columns(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        assert column_names(connection, "gmail_diagnostics") == {
            "id",
            "outcome",
            "request_count",
            "page_count",
            "projected_count",
            "excluded_count",
            "byte_budget",
        }
        assert column_names(connection, "gmail_diagnostic_reason_counts") == {
            "diagnostic_id",
            "reason",
            "count",
        }
        assert column_names(connection, "confirmed_delivery_receipts") == {
            "receipt_token",
            "delivered_count",
            "confirmed_at",
        }


def test_confirmed_delivery_receipts_are_immutable(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        _ = connection.execute(
            """
            INSERT INTO confirmed_delivery_receipts
                (receipt_token, delivered_count, confirmed_at)
            VALUES ('opaque-token', 2, '2026-08-25T12:00:00+00:00')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "UPDATE confirmed_delivery_receipts SET delivered_count = 3"
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("DELETE FROM confirmed_delivery_receipts")
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "INSERT INTO confirmed_delivery_receipts VALUES ('negative', -1, 'now')"
            )
        assert connection.execute(
            "SELECT * FROM confirmed_delivery_receipts"
        ).fetchone() == ("opaque-token", 2, "2026-08-25T12:00:00+00:00")


def test_insert_or_replace_cannot_overwrite_confirmed_receipt(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        original = ("opaque-token", 2, "2026-08-25T12:00:00+00:00")
        _ = connection.execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, ?, ?)", original
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT OR REPLACE INTO confirmed_delivery_receipts
                VALUES ('opaque-token', 99, '2030-01-01T00:00:00+00:00')
                """
            )

        assert (
            connection.execute("SELECT * FROM confirmed_delivery_receipts").fetchone()
            == original
        )
