"""Coherent SQLite snapshots of source state and Gmail diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ._sync_models import (
    SourceHealthSnapshot,
    SourceReadDiagnostics,
    SourceSyncState,
    unconfigured_state,
)

if TYPE_CHECKING:
    import sqlite3

_SOURCE_SYNC_STATE_ADAPTER: Final[TypeAdapter[SourceSyncState]] = TypeAdapter(
    SourceSyncState
)
_SOURCE_READ_DIAGNOSTICS_ADAPTER: Final[TypeAdapter[SourceReadDiagnostics]] = (
    TypeAdapter(SourceReadDiagnostics)
)


class SourceSnapshotStore:
    """Own capture callbacks and coherent reads on one SQLite connection."""

    _connection: sqlite3.Connection
    _states: list[SourceSyncState]
    _diagnostics: list[SourceReadDiagnostics]

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._states = []
        self._diagnostics = []
        connection.create_function(
            "_proactive_capture_source_sync_state",
            1,
            self._capture_state,
        )
        connection.create_function(
            "_proactive_capture_gmail_diagnostics",
            1,
            self._capture_diagnostics,
        )

    def health(self) -> SourceHealthSnapshot:
        """Return source state and diagnostics from one SQLite read snapshot."""
        self._states.clear()
        self._diagnostics.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(captured) FROM (
                SELECT
                    CASE source WHEN 'gmail' THEN 0 ELSE 1 END AS ordering,
                    _proactive_capture_source_sync_state(json_object(
                        'source', source,
                        'auth_state', auth_state,
                        'last_success_at', last_success_at,
                        'last_attempt_at', last_attempt_at,
                        'last_error_code', last_error_code,
                        'sync_cursor', sync_cursor,
                        'updated_at', updated_at
                    )) AS captured
                FROM source_sync_state
                UNION ALL
                SELECT 2, _proactive_capture_gmail_diagnostics(json_object(
                    'outcome', outcome,
                    'request_count', request_count,
                    'page_count', page_count,
                    'projected_count', projected_count,
                    'excluded_count', excluded_count,
                    'byte_budget', byte_budget,
                    'reason_counts', json(COALESCE((
                        SELECT json_group_array(json_object(
                            'reason', reason,
                            'count', count
                        ))
                        FROM (
                            SELECT reason, count
                            FROM gmail_diagnostic_reason_counts
                            WHERE diagnostic_id = gmail_diagnostics.id
                            ORDER BY reason
                        )
                    ), '[]'))
                ))
                FROM gmail_diagnostics WHERE id = 1
                ORDER BY ordering
            )
            """
        )
        persisted = {state.source: state for state in self._states}
        return SourceHealthSnapshot(
            gmail=persisted.get("gmail", unconfigured_state("gmail")),
            calendar=persisted.get("calendar", unconfigured_state("calendar")),
            gmail_diagnostics=None if not self._diagnostics else self._diagnostics[0],
        )

    def gmail_diagnostics(self) -> SourceReadDiagnostics | None:
        """Return the latest accepted Gmail diagnostics, when present."""
        self._diagnostics.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(_proactive_capture_gmail_diagnostics(json_object(
                'outcome', outcome,
                'request_count', request_count,
                'page_count', page_count,
                'projected_count', projected_count,
                'excluded_count', excluded_count,
                'byte_budget', byte_budget,
                'reason_counts', json(COALESCE((
                    SELECT json_group_array(json_object(
                        'reason', reason,
                        'count', count
                    ))
                    FROM (
                        SELECT reason, count
                        FROM gmail_diagnostic_reason_counts
                        WHERE diagnostic_id = gmail_diagnostics.id
                        ORDER BY reason
                    )
                ), '[]'))
            )))
            FROM gmail_diagnostics WHERE id = 1
            """
        )
        return None if not self._diagnostics else self._diagnostics[0]

    def states(self) -> tuple[SourceSyncState, ...]:
        """Return persisted source rows in stable Google source order."""
        self._states.clear()
        _ = self._connection.execute(
            """
            SELECT SUM(_proactive_capture_source_sync_state(
                json_object(
                    'source', source,
                    'auth_state', auth_state,
                    'last_success_at', last_success_at,
                    'last_attempt_at', last_attempt_at,
                    'last_error_code', last_error_code,
                    'sync_cursor', sync_cursor,
                    'updated_at', updated_at
                )
            ))
            FROM (
                SELECT * FROM source_sync_state
                ORDER BY CASE source WHEN 'gmail' THEN 0 ELSE 1 END
            )
            """
        )
        return tuple(self._states)

    def _capture_state(self, payload: str) -> int:
        self._states.append(_SOURCE_SYNC_STATE_ADAPTER.validate_json(payload))
        return 1

    def _capture_diagnostics(self, payload: str) -> int:
        diagnostics = _SOURCE_READ_DIAGNOSTICS_ADAPTER.validate_json(payload)
        self._diagnostics.append(diagnostics)
        return 1
