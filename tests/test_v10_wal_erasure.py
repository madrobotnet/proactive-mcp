from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.store import ReceiptErasurePendingError, Store
from tests.store_migration_support import capture_strings, scalar_int
from tests.v10_migration_support import create_v9_database, insert_v9_claim


def _artifact_hits(path: Path, canary: bytes) -> dict[str, int]:
    artifacts = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    return {
        artifact.name: artifact.read_bytes().count(canary) if artifact.exists() else 0
        for artifact in artifacts
    }


def _maintenance_pending(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        row = cast(
            "tuple[int] | None",
            connection.execute(
                """
                SELECT COUNT(*) FROM migration_maintenance
                WHERE task = 'v9_receipt_erasure' AND pending = 1
                """
            ).fetchone(),
        )
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def _open_current_at_barrier(path: Path, barrier: Barrier) -> int:
    assert barrier.wait(timeout=10) >= 0
    with Store(path, busy_timeout_ms=0) as store:
        return store.status().migration_version


def test_v10_invalidates_v9_raw_receipt_claims_without_mutating_situations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    receipt_canary = "PR29_V9_ACTIVE_RECEIPT_CANARY_k7P2rN9xV4mQ8wD3"
    _ = create_v9_database(path)
    insert_v9_claim(path, receipt_canary)

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
        assert _artifact_hits(path, receipt_canary.encode()) == {
            "legacy.db": 0,
            "legacy.db-wal": 0,
            "legacy.db-shm": 0,
        }


def test_pinned_v9_reader_fails_closed_then_retry_erases_and_clears_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "legacy-secret-path.db"
    receipt_canary = "PR29_PINNED_RECEIPT_CANARY_a8F4zR2nW7qL5cX9"
    _ = create_v9_database(path)
    insert_v9_claim(path, receipt_canary)

    legacy_reader = sqlite3.connect(path)
    legacy_reader.execute("PRAGMA journal_mode = WAL").close()
    legacy_reader.execute("BEGIN").close()
    row = cast(
        "tuple[str] | None",
        legacy_reader.execute(
            "SELECT claim_token FROM situation_delivery_claims"
        ).fetchone(),
    )
    assert row == (receipt_canary,)

    with pytest.raises(
        ReceiptErasurePendingError,
        match="receipt erasure is blocked; close older processes and retry",
    ) as failure:
        _ = Store(path, busy_timeout_ms=0)
    assert receipt_canary not in str(failure.value)
    assert str(path) not in str(failure.value)
    assert _maintenance_pending(path) == 1
    assert legacy_reader.execute(
        "SELECT claim_token FROM situation_delivery_claims"
    ).fetchone() == (receipt_canary,)
    assert sum(_artifact_hits(path, receipt_canary.encode()).values()) > 0

    monkeypatch.setenv("PROACTIVE_DATABASE", str(path))
    monkeypatch.setattr(
        daemon_cli,
        "Store",
        partial(Store, busy_timeout_ms=0),
    )
    assert daemon_cli.run_daemon(once=True, poll_interval_minutes=None) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "database", "code": "open_failed"}
    assert receipt_canary not in captured.err
    assert str(path) not in captured.err
    assert _maintenance_pending(path) == 1

    legacy_reader.rollback()
    legacy_reader.close()
    with Store(path, busy_timeout_ms=0):
        assert _maintenance_pending(path) == 0
        assert _artifact_hits(path, receipt_canary.encode()) == {
            "legacy-secret-path.db": 0,
            "legacy-secret-path.db-wal": 0,
            "legacy-secret-path.db-shm": 0,
        }

        current_reader = sqlite3.connect(path)
        try:
            current_reader.execute("BEGIN").close()
            assert current_reader.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone() == (12,)
            barrier = Barrier(3)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(_open_current_at_barrier, path, barrier)
                    for _ in range(2)
                ]
                assert barrier.wait(timeout=10) >= 0
            assert [future.result(timeout=10) for future in futures] == [12, 12]
        finally:
            current_reader.rollback()
            current_reader.close()
