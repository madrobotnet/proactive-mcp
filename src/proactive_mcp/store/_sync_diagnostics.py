"""Validation and persistence for bounded Gmail read diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ._sync_models import (
    GMAIL_READ_BYTE_BUDGET,
    InvalidSourceReadDiagnosticsError,
)

if TYPE_CHECKING:
    import sqlite3

    from ._sync_models import SourceReadDiagnostics

_DIAGNOSTIC_COUNT_MAXIMA: Final[tuple[int, ...]] = (
    221,
    20,
    200,
    2_000,
    GMAIL_READ_BYTE_BUDGET,
)
_MAX_DIAGNOSTIC_REASON_COUNT: Final[int] = 200
_SOURCE_READ_OUTCOMES: Final = frozenset(
    {"healthy", "partial", "stale", "auth_error", "transport_error"}
)
_SOURCE_READ_REASONS: Final = frozenset(
    {
        "body_snippet_fallback",
        "body_truncated",
        "degraded",
        "direction_metadata_ambiguous",
        "direction_metadata_missing",
        "http_4xx",
        "http_5xx",
        "identity_headers_ambiguous",
        "invalid_grant",
        "mime_structure_truncated",
        "network",
        "never_synced",
        "not_configured",
        "pagination_limit",
        "resource_limit",
        "scope_mismatch",
        "stale",
        "sync_budget_exhausted",
        "thread_list_entry_skipped",
        "thread_projection_limit",
        "thread_response_too_large",
        "thread_without_projectable_message",
        "timeout",
        "unknown",
    }
)


class GmailDiagnosticsStore:
    """Validate and persist the accepted singleton Gmail diagnostics."""

    _connection: sqlite3.Connection

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def validate(self, diagnostics: SourceReadDiagnostics) -> None:
        """Reject values outside the closed and bounded diagnostics contract."""
        counts = (
            diagnostics.request_count,
            diagnostics.page_count,
            diagnostics.projected_count,
            diagnostics.excluded_count,
            diagnostics.byte_budget,
        )
        reasons = tuple(item.reason for item in diagnostics.reason_counts)
        if (
            diagnostics.outcome not in _SOURCE_READ_OUTCOMES
            or any(
                type(value) is not int or not 0 <= value <= maximum
                for value, maximum in zip(counts, _DIAGNOSTIC_COUNT_MAXIMA, strict=True)
            )
            or len(reasons) != len(set(reasons))
            or any(
                item.reason not in _SOURCE_READ_REASONS
                or type(item.count) is not int
                or not 0 <= item.count <= _MAX_DIAGNOSTIC_REASON_COUNT
                for item in diagnostics.reason_counts
            )
        ):
            raise InvalidSourceReadDiagnosticsError

    def write(self, diagnostics: SourceReadDiagnostics) -> None:
        """Replace the singleton and all of its normalized reason counters."""
        _ = self._connection.execute(
            "DELETE FROM gmail_diagnostic_reason_counts WHERE diagnostic_id = 1"
        )
        _ = self._connection.execute(
            """
            INSERT INTO gmail_diagnostics (
                id, outcome, request_count, page_count, projected_count,
                excluded_count, byte_budget
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                outcome = excluded.outcome,
                request_count = excluded.request_count,
                page_count = excluded.page_count,
                projected_count = excluded.projected_count,
                excluded_count = excluded.excluded_count,
                byte_budget = excluded.byte_budget
            """,
            (
                diagnostics.outcome,
                diagnostics.request_count,
                diagnostics.page_count,
                diagnostics.projected_count,
                diagnostics.excluded_count,
                diagnostics.byte_budget,
            ),
        )
        for item in diagnostics.reason_counts:
            _ = self._connection.execute(
                """
                INSERT INTO gmail_diagnostic_reason_counts (
                    diagnostic_id, reason, count
                ) VALUES (1, ?, ?)
                """,
                (item.reason, item.count),
            )
