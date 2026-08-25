from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import TYPE_CHECKING, Final, TypeAlias

import pytest
from pydantic import TypeAdapter

from proactive_mcp.store import Store
from proactive_mcp.store.migrate import apply_migrations
from proactive_mcp.store.migrations import load_migrations
from tests.store_migration_support import (
    capture_ints,
    capture_json_rows,
    capture_strings,
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


def test_v10_invalidates_v9_raw_receipt_claims_without_mutating_situations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    receipt_canary = "PR29_V9_ACTIVE_RECEIPT_CANARY_k7P2rN9xV4mQ8wD3"
    _ = _create_v9_database(path)
    connection = sqlite3.connect(path)
    try:
        _ = connection.execute(
            """
            INSERT INTO situations (
                situation_type, dedupe_key, state, priority, title, why_now,
                evidence, detected_at, updated_at
            ) VALUES (
                'reply_deadline', 'v9-active-claim', 'pending', 'routine',
                'fixture', 'fixture', '{}', ?, ?
            )
            """,
            ("2026-08-25T12:00:00+00:00", "2026-08-25T12:00:00+00:00"),
        )
        situation_id = scalar_int(
            connection,
            "SELECT id FROM situations WHERE dedupe_key = 'v9-active-claim'",
        )
        _ = connection.execute(
            """
            INSERT INTO situation_delivery_claims (
                claim_token, situation_id, claimed_at, expires_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                receipt_canary,
                situation_id,
                "2026-08-25T12:00:00+00:00",
                "2026-08-25T12:02:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with Store(path) as upgraded:
        assert capture_strings(
            upgraded.connection(),
            "SELECT _cap_str(state) FROM situations WHERE dedupe_key = ?",
            ("v9-active-claim",),
        ) == ["pending"]
        assert (
            scalar_int(
                upgraded.connection(), "SELECT COUNT(*) FROM situation_delivery_claims"
            )
            == 0
        )
        assert receipt_canary not in "\n".join(upgraded.connection().iterdump())

    assert all(
        receipt_canary.encode() not in artifact.read_bytes()
        for artifact in tmp_path.iterdir()
    )


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
