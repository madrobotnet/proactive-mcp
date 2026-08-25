from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Final, Literal, NoReturn, TypeAlias

import pytest

from proactive_mcp import cli
from proactive_mcp.cli.service_models import ServiceResponse
from proactive_mcp.delivery.daemon import (
    DaemonFailureError,
    DaemonFailureKind,
    run_daemon_phase,
)
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_responses import source_read_diagnostics_response
from proactive_mcp.server.status import DaemonDiagnosticResponse, status_response
from proactive_mcp.sources.google_sync import (
    GmailProfileReader,
    GoogleReadDependencies,
    GoogleSyncService,
    GoogleTransportError,
    InvalidGrantError,
)
from proactive_mcp.store import Store
from tests.situation_test_support import FakeClock, utc_datetime
from tests.test_google_sync import (
    FailingGmailReader,
    FakeCalendarReader,
    FakeCredentials,
    FakeInboxReader,
    calendar_result,
    gmail_inbox_result,
)

_CANARIES: Final = (
    "message_7f34c912_body",
    "addr_45e6a1@example.invalid",
    "subject_b829d3e1",
    "/private/diag-path-91ca7f",
    "oauth_6e31c4_client",
    "token_98f2b0_bearer",
    "exception_4ca65f_arbitrary",
    "Traceback (most recent call last): frame_73d0",
)
_SOURCE_FIELDS: Final = {
    "outcome",
    "request_count",
    "page_count",
    "projected_count",
    "excluded_count",
    "byte_budget",
    "reason_counts",
}
SourceScenario: TypeAlias = Literal["normal", "truncated", "budget", "auth", "network"]
_SERVICE_FIELDS: Final = {
    "action",
    "state",
    "unit",
    "managed",
    "enabled",
    "active",
    "main_pid",
    "heartbeat",
    "linger",
    "guidance",
    "code",
}


def _assert_private_json(serialized: str, expected_fields: set[str]) -> None:
    assert all(canary not in serialized for canary in _CANARIES)
    assert "Traceback" not in serialized
    assert "path" not in expected_fields


def _source_diagnostic_json(tmp_path: Path, scenario: SourceScenario) -> str:
    base = gmail_inbox_result()
    hostile_text = " ".join(_CANARIES)
    thread = replace(
        base.threads[0],
        thread_id=_CANARIES[5],
        latest_message_id=_CANARIES[0],
        subject=_CANARIES[2],
        sender_display=_CANARIES[1],
        snippet=_CANARIES[3],
        body_text=hostile_text,
        provider_history_cursor=_CANARIES[4],
    )
    result = replace(base, threads=(thread,), provider_history_cursor=_CANARIES[5])
    readers: dict[SourceScenario, GmailProfileReader] = {
        "normal": FakeInboxReader(result),
        "truncated": FakeInboxReader(
            replace(
                result,
                degradation_reasons=("body_truncated",),
                degradation_reason_counts=(("body_truncated", 1),),
            )
        ),
        "budget": FakeInboxReader(
            replace(
                result,
                coverage_complete=False,
                degradation_reasons=("sync_budget_exhausted",),
            )
        ),
        "auth": FailingGmailReader(InvalidGrantError()),
        "network": FailingGmailReader(GoogleTransportError("network")),
    }
    gmail = readers[scenario]
    with Store(tmp_path / f"{scenario}.db") as store:
        summary = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=gmail,
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        ).sync()
    response = source_read_diagnostics_response(summary.gmail_diagnostics)
    assert set(response.model_dump()) == _SOURCE_FIELDS
    return response.model_dump_json()


@pytest.mark.parametrize(
    "scenario",
    ["normal", "truncated", "budget", "auth", "network"],
)
def test_source_diagnostics_exclude_untrusted_values_across_outcomes(
    tmp_path: Path,
    scenario: SourceScenario,
) -> None:
    # Given: canaries occupy every source-content position and outcome class.
    # When: the public source diagnostic response is serialized.
    serialized = _source_diagnostic_json(tmp_path, scenario)
    # Then: only closed counters, outcomes, and reason codes remain.
    _assert_private_json(serialized, _SOURCE_FIELDS)


@pytest.mark.parametrize("kind", tuple(DaemonFailureKind))
def test_daemon_diagnostics_exclude_exception_data_for_every_phase(
    kind: DaemonFailureKind,
) -> None:
    # Given: every phase raises hostile nested exception data.
    def fail() -> NoReturn:
        raise RuntimeError({"warnings": [_CANARIES, {"alias": _CANARIES[6]}]})

    # When: the public daemon phase boundary normalizes the failure.
    with pytest.raises(DaemonFailureError) as caught:
        _ = run_daemon_phase(kind, fail)
    response = DaemonDiagnosticResponse(
        phase=caught.value.phase,
        code=caught.value.code,
    )
    # Then: only phase and code cross the serializer.
    assert set(response.model_dump()) == {"phase", "code"}
    _assert_private_json(response.model_dump_json(), {"phase", "code"})


def test_service_layout_failure_serializes_only_closed_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: layout resolution raises synthetic nested untrusted diagnostics.
    def fail_layout() -> NoReturn:
        raise OSError(
            _CANARIES[6],
            {"journal": [_CANARIES, {"address": _CANARIES[1]}]},
            _CANARIES[3],
        )

    monkeypatch.setenv("OAUTH_VALUE", _CANARIES[4])
    monkeypatch.setenv("ACCESS_TOKEN", _CANARIES[5])
    monkeypatch.setattr("proactive_mcp.cli.service._layout", fail_layout)
    # When: service status crosses the public CLI adapter.
    result = cli.main(["service", "status"])
    captured = capsys.readouterr()
    response = ServiceResponse.model_validate_json(captured.out)
    # Then: one closed typed response contains no exception or path data.
    assert result == 2
    assert captured.err == ""
    assert response.action == "status"
    assert response.state == "failed"
    assert response.code == "io_failed"
    assert set(response.model_dump()) == _SERVICE_FIELDS
    _assert_private_json(captured.out, _SERVICE_FIELDS)


def test_legacy_database_path_is_the_only_absolute_status_path(tmp_path: Path) -> None:
    # Given: a migration-9 store with a private source cursor.
    database = tmp_path / "state" / "proactive.db"
    paths = ProactivePaths.for_database(database)
    clock = FakeClock(utc_datetime(2026, 8, 25, 12))
    with Store(database, clock=clock) as store:
        store.record_sync_success("gmail", sync_cursor=_CANARIES[5])
        # When: the public status adapter serializes compatibility and diagnostics.
        status = status_response(store, clock, paths)
    serialized = status.model_dump_json()
    # Then: only legacy database.path is a path-bearing status field.
    assert status.database.path == str(database.absolute())
    assert Path(status.database.path).is_absolute()
    assert serialized.count('"path":') == 1
    assert serialized.count(str(database.absolute())) == 1
    assert "path" not in status.google.gmail.diagnostics.model_dump()
    assert set(status.google.gmail.diagnostics.model_dump()) == _SOURCE_FIELDS
    assert _CANARIES[5] not in serialized
