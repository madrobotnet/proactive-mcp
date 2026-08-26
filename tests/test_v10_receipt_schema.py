import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from proactive_mcp.store import DeliveryReceiptError, Store
from tests.store_migration_support import column_names


@pytest.mark.parametrize(
    ("receipt_digest", "confirmed_at"),
    [(b"", "2026-08-25T12:00:00+00:00"), (bytes(32), "")],
)
def test_confirmed_receipts_reject_empty_structural_fields(
    tmp_path: Path,
    receipt_digest: bytes,
    confirmed_at: str,
) -> None:
    with Store(tmp_path / "db") as store, pytest.raises(sqlite3.IntegrityError):
        _ = store.connection().execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, 1, ?)",
            (receipt_digest, confirmed_at),
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
            "receipt_digest",
            "delivered_count",
            "confirmed_at",
        }


def test_confirmed_delivery_receipts_are_immutable(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        digest = sha256(b"opaque-token").digest()
        _ = connection.execute(
            """
            INSERT INTO confirmed_delivery_receipts
                (receipt_digest, delivered_count, confirmed_at)
            VALUES (?, 2, '2026-08-25T12:00:00+00:00')
            """,
            (digest,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "UPDATE confirmed_delivery_receipts SET delivered_count = 3"
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute("DELETE FROM confirmed_delivery_receipts")
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "INSERT INTO confirmed_delivery_receipts VALUES (?, -1, 'now')",
                (sha256(b"negative").digest(),),
            )
        assert connection.execute(
            "SELECT * FROM confirmed_delivery_receipts"
        ).fetchone() == (digest, 2, "2026-08-25T12:00:00+00:00")


def test_insert_or_replace_cannot_overwrite_confirmed_receipt(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        original = (
            sha256(b"opaque-token").digest(),
            2,
            "2026-08-25T12:00:00+00:00",
        )
        _ = connection.execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, ?, ?)", original
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT OR REPLACE INTO confirmed_delivery_receipts
                VALUES (?, 99, '2030-01-01T00:00:00+00:00')
                """,
                (original[0],),
            )

        assert (
            connection.execute("SELECT * FROM confirmed_delivery_receipts").fetchone()
            == original
        )


@pytest.mark.parametrize(
    "conflict_sql",
    [
        """
        INSERT OR IGNORE INTO confirmed_delivery_receipts VALUES (?, 99, 'forged')
        """,
        """
        INSERT INTO confirmed_delivery_receipts VALUES (?, 99, 'forged')
        ON CONFLICT(receipt_digest) DO NOTHING
        """,
        """
        INSERT INTO confirmed_delivery_receipts VALUES (?, 99, 'forged')
        ON CONFLICT(receipt_digest) DO UPDATE SET delivered_count = 99
        """,
    ],
)
def test_every_insert_conflict_form_rejects_receipt_replacement(
    tmp_path: Path,
    conflict_sql: str,
) -> None:
    original = (
        sha256(b"conflict-receipt").digest(),
        2,
        "2026-08-25T12:00:00+00:00",
    )
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        _ = connection.execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, ?, ?)", original
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            _ = connection.execute(conflict_sql, (original[0],))
        assert (
            connection.execute("SELECT * FROM confirmed_delivery_receipts").fetchone()
            == original
        )


def test_hidden_rowid_replace_cannot_forge_replay_result(tmp_path: Path) -> None:
    with Store(tmp_path / "db") as store:
        connection = store.connection()
        original = (
            sha256(b"original-receipt").digest(),
            1,
            "2026-08-25T12:00:00+00:00",
        )
        _ = connection.execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, ?, ?)", original
        )

        with pytest.raises(sqlite3.OperationalError, match="rowid"):
            _ = connection.execute(
                """
                INSERT OR REPLACE INTO confirmed_delivery_receipts(
                    rowid, receipt_digest, delivered_count, confirmed_at
                ) VALUES (1, ?, 99, '2030-01-01T00:00:00+00:00')
                """,
                (sha256(b"forged-receipt").digest(),),
            )

        assert (
            connection.execute("SELECT * FROM confirmed_delivery_receipts").fetchone()
            == original
        )
        replay = store.situations.confirm_delivery("original-receipt")
        assert (replay.status, replay.delivered_count) == ("already_confirmed", 1)
        with pytest.raises(DeliveryReceiptError):
            _ = store.situations.confirm_delivery("forged-receipt")


@pytest.mark.parametrize("invalid_count", [0.5, b"1", -1, 101])
def test_confirmed_receipt_count_is_a_bounded_integer(
    tmp_path: Path,
    invalid_count: object,
) -> None:
    with Store(tmp_path / "db") as store, pytest.raises(sqlite3.IntegrityError):
        _ = store.connection().execute(
            "INSERT INTO confirmed_delivery_receipts VALUES (?, ?, 'now')",
            (sha256(b"typed-count").digest(), invalid_count),
        )
