from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Final, cast, get_args

import pytest

from proactive_mcp.store import Store
from proactive_mcp.store.sync import (
    InvalidSourceReadDiagnosticsError,
    SourceReadDiagnostics,
    SourceReadOutcome,
    SourceReadReason,
    SourceReadReasonCount,
)

if TYPE_CHECKING:
    from pathlib import Path

_OUTCOMES: Final = cast("tuple[str, ...]", get_args(SourceReadOutcome))
_REASONS: Final = cast("tuple[str, ...]", get_args(SourceReadReason))
_INSERT_DIAGNOSTICS: Final = """
    INSERT INTO gmail_diagnostics (
        id, outcome, request_count, page_count, projected_count,
        excluded_count, byte_budget
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """


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


@pytest.mark.parametrize(
    ("column", "maximum"),
    [
        ("request_count", 221),
        ("page_count", 20),
        ("projected_count", 200),
        ("excluded_count", 2_000),
        ("byte_budget", 8_000_000),
    ],
)
@pytest.mark.parametrize("invalid_kind", ["real", "blob", "overbound"])
def test_diagnostic_counters_require_bounded_integer_storage(
    tmp_path: Path,
    column: str,
    maximum: int,
    invalid_kind: str,
) -> None:
    invalid: object = {
        "real": 0.5,
        "blob": b"1",
        "overbound": maximum + 1,
    }[invalid_kind]
    with Store(tmp_path / "typed.db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DIAGNOSTICS, (1, "partial", 0, 0, 0, 0, 0))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                f"UPDATE gmail_diagnostics SET {column} = ?",  # noqa: S608
                (invalid,),
            )


@pytest.mark.parametrize("invalid_count", [0.5, b"1", -1, 201])
def test_reason_count_requires_bounded_integer_storage(
    tmp_path: Path,
    invalid_count: object,
) -> None:
    with Store(tmp_path / "typed-reason.db") as store:
        connection = store.connection()
        _ = connection.execute(_INSERT_DIAGNOSTICS, (1, "partial", 0, 0, 0, 0, 0))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO gmail_diagnostic_reason_counts
                    (diagnostic_id, reason, count)
                VALUES (1, 'stale', ?)
                """,
                (invalid_count,),
            )


@pytest.mark.parametrize(
    "column",
    [
        "request_count",
        "page_count",
        "projected_count",
        "excluded_count",
        "byte_budget",
    ],
)
def test_store_boundary_rejects_boolean_diagnostic_counters(
    tmp_path: Path,
    column: str,
) -> None:
    diagnostics = SourceReadDiagnostics("partial", 0, 0, 0, 0, 0)
    malformed = replace(diagnostics, **{column: True})
    with (
        Store(tmp_path / "boolean.db") as store,
        pytest.raises(InvalidSourceReadDiagnosticsError),
    ):
        store.record_gmail_sync(malformed)


def test_store_boundary_rejects_boolean_diagnostic_reason_count(
    tmp_path: Path,
) -> None:
    diagnostics = SourceReadDiagnostics(
        "partial",
        0,
        0,
        0,
        0,
        0,
        (SourceReadReasonCount("stale", count=True),),
    )
    with (
        Store(tmp_path / "boolean-reason.db") as store,
        pytest.raises(InvalidSourceReadDiagnosticsError),
    ):
        store.record_gmail_sync(diagnostics)


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
