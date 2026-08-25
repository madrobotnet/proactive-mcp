import sqlite3
from pathlib import Path

import pytest

from proactive_mcp.store import Store
from tests.store_migration_support import column_names


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
