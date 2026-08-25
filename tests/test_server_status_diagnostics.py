from pathlib import Path

import pytest

from proactive_mcp.delivery.daemon import DaemonFailureError, DaemonFailureKind
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.status import DaemonDiagnosticResponse, status_response
from proactive_mcp.store import SourceSyncFailureCode, Store
from proactive_mcp.store.sync import SourceReadDiagnostics, SourceReadReasonCount
from tests.situation_test_support import FakeClock, utc_datetime


@pytest.mark.parametrize(
    ("error_code", "expected_outcome"),
    [
        ("degraded", "partial"),
        ("network", "transport_error"),
    ],
)
def test_status_maps_persisted_gmail_failures_to_typed_outcomes(
    tmp_path: Path,
    error_code: SourceSyncFailureCode,
    expected_outcome: str,
) -> None:
    # Given: one normalized Gmail failure in existing migration-9 state.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_failure("gmail", error_code=error_code)

        # When: status projects the persisted freshness/error state.
        status = status_response(store, clock, paths)

    # Then: legacy freshness remains while the additive outcome is distinct.
    assert status.google.gmail.status == "error"
    assert status.google.gmail.error_code == error_code
    assert status.google.gmail.diagnostics.outcome == expected_outcome
    assert status.google.gmail.diagnostics.reason_counts == {error_code: 1}


def test_status_uses_persisted_diagnostics_after_first_v10_attempt(
    tmp_path: Path,
) -> None:
    # Given: migration-9-compatible freshness exists before any diagnostic attempt.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    diagnostics = SourceReadDiagnostics(
        outcome="partial",
        request_count=8,
        page_count=2,
        projected_count=3,
        excluded_count=5,
        byte_budget=8_000_000,
        reason_counts=(SourceReadReasonCount("pagination_limit", 1),),
    )
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        store.record_sync_success("calendar")
        assert store.gmail_diagnostics() is None
        compatibility = status_response(store, clock, paths)

        # When: the first v10-aware Gmail attempt records bounded diagnostics.
        store.record_gmail_sync(diagnostics, error_code="degraded")
        current = status_response(store, clock, paths)

    # Then: the empty-table default is legacy-derived, then persisted data wins.
    assert compatibility.google.gmail.diagnostics.outcome == "healthy"
    assert compatibility.google.gmail.diagnostics.request_count == 0
    assert current.google.gmail.diagnostics.model_dump() == {
        "outcome": "partial",
        "request_count": 8,
        "page_count": 2,
        "projected_count": 3,
        "excluded_count": 5,
        "byte_budget": 8_000_000,
        "reason_counts": {"pagination_limit": 1},
    }


@pytest.mark.parametrize("kind", tuple(DaemonFailureKind))
def test_daemon_diagnostic_taxonomy_serializes_only_phase_and_code(
    kind: DaemonFailureKind,
) -> None:
    # Given: one member of the closed daemon failure taxonomy.
    failure = DaemonFailureError(kind)

    # When: the journal-facing response is serialized.
    payload = DaemonDiagnosticResponse(phase=failure.phase, code=failure.code)

    # Then: only bounded routing values cross the status boundary.
    assert payload.model_dump() == {"phase": failure.phase, "code": failure.code}
