import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

from proactive_mcp.config import load_config
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server import StatusResponse, build_status
from proactive_mcp.server.status import status_response
from proactive_mcp.store import FallbackClaim, Store
from tests.daemon_cli_test_support import start_live_overridden_watcher
from tests.situation_test_support import FakeClock, utc_datetime
from tests.situation_tool_support import UNTRUSTED_SUBJECT, pending_detection

_PID = 4242
_FALLBACK_WAIT = timedelta(minutes=30)


@pytest.mark.anyio
async def test_get_status_over_stdio(tmp_path: Path) -> None:
    # Given: the packaged server running over stdio on a fresh installation.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proactive_mcp.server"],
        env={"PROACTIVE_DATABASE": str(tmp_path / "status.db")},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        _ = await session.initialize()

        # When: an agent asks for status.
        result = await session.call_tool("get_status")

    # Then: every surface reports itself, and nothing claims to be healthy.
    content = result.content[0]
    assert isinstance(content, TextContent)
    status = StatusResponse.model_validate_json(content.text)
    assert status.database.status == "healthy"
    assert status.google.gmail.status == "not_configured"
    assert status.google.gmail.last_success_at is None
    assert status.google.gmail.last_attempt_at is None
    assert status.google.gmail.age_seconds is None
    assert status.google.gmail.error_code is None
    assert status.google.calendar.status == "not_configured"
    assert status.daemon.status == "not_running"
    assert status.daemon.liveness == "never_started"
    assert status.daemon.pid is None
    assert status.daemon.cycle_count == 0
    assert status.fallback.claimed == 0
    assert status.fallback.sent == 0
    assert status.fallback.failed == 0
    assert status.fallback.failure_codes == ()
    assert status.budget.daily_budget == 4
    assert status.deliveries.total == 0
    assert status.overall == "degraded"
    assert status.warnings
    assert "all-clear" not in status.model_dump_json().lower()


def test_status_reports_stale_and_reauthentication_required_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an expired Gmail success and a shared Google grant that needs consent.
    database_path = tmp_path / "status.db"
    clock = FakeClock(utc_datetime(2000, 1, 1))
    with Store(database_path, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail", sync_cursor="private-cursor")
        store.record_google_invalid_grant()
        store.set_source_auth("gmail", "configured")
        store.record_sync_success("gmail")
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))

    # When: the server builds its public status response.
    status = build_status()

    # Then: each source carries a non-all-clear actionable freshness state.
    assert status.google.gmail.status == "stale"
    assert status.google.gmail.last_success_at is not None
    assert status.google.gmail.age_seconds is not None
    assert status.google.gmail.error_code is None
    assert status.google.gmail.diagnostics.outcome == "stale"
    assert status.google.calendar.status == "needs_reauth"
    assert status.google.calendar.error_code == "invalid_grant"
    assert status.google.calendar.model_dump().get("diagnostics") is None
    assert status.overall == "degraded"
    assert status.warnings
    diagnostic_payload = status.google.gmail.diagnostics.model_dump()
    assert "path" not in diagnostic_payload
    assert status.database.path == str(database_path.absolute())
    assert "private-cursor" not in status.model_dump_json()


def test_status_reports_daemon_liveness_and_redacted_fallback_failures(
    tmp_path: Path,
) -> None:
    # Given: a current daemon heartbeat and one failed OS notification.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(paths.database, clock=clock) as store:
        _ = store.situations.upsert_detections(
            (pending_detection("toast", "critical"),)
        )
        clock.advance(_FALLBACK_WAIT + timedelta(minutes=1))
        store.daemon.record_start(pid=_PID)
        store.daemon.record_heartbeat()
        claimed = store.fallbacks.claim_next(
            FallbackClaim(
                claimed_at=clock.now().isoformat(),
                detected_before=(clock.now() - _FALLBACK_WAIT).isoformat(),
                priorities=("critical",),
            )
        )
        assert claimed is not None
        store.fallbacks.record_failed(claimed.id, code="nonzero_exit")

        # When: the status document is built for that installation.
        status = status_response(store, clock, paths)

    # Then: daemon and fallback state are structured, coded, and PII-free.
    assert status.daemon.status == "running"
    assert status.daemon.liveness == "running"
    assert status.daemon.pid == _PID
    assert status.daemon.cycle_count == 1
    assert status.daemon.heartbeat_at is not None
    assert status.fallback.claimed == 0
    assert status.fallback.sent == 0
    assert status.fallback.failed == 1
    assert status.fallback.failure_codes == ("nonzero_exit",)
    assert UNTRUSTED_SUBJECT not in status.model_dump_json()
    assert status.overall == "degraded"


def test_status_is_healthy_only_when_no_surface_warns(tmp_path: Path) -> None:
    # Given: fresh Google sources and a live daemon heartbeat.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        store.record_sync_success("calendar")
        store.daemon.record_start(pid=_PID)
        store.daemon.record_heartbeat()

        # When: the status document is built for that installation.
        status = status_response(store, clock, paths)

    # Then: nothing warns and the installation reports itself healthy.
    assert status.google.gmail.status == "ok"
    assert status.google.gmail.diagnostics.outcome == "healthy"
    assert status.daemon.liveness == "running"
    assert status.warnings == ()
    assert status.overall == "ok"


def test_status_keeps_a_sixty_minute_override_daemon_running_at_sixteen_minutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 5-minute config and a still-running daemon started at 60 minutes.
    paths, clock = start_live_overridden_watcher(tmp_path, monkeypatch)

    # When: status is built 16 minutes after the last heartbeat.
    clock.advance(timedelta(minutes=16))
    with Store(paths.database, clock=clock) as store:
        status = status_response(store, clock, paths)

    # Then: the override is visible, so the watcher is not reported stale.
    assert load_config(paths.config).daemon.poll_interval == timedelta(minutes=5)
    assert status.daemon.liveness == "running"
    assert status.daemon.status == "running"
    assert all("heartbeat is stale" not in warning for warning in status.warnings)


def test_status_counts_critical_deliveries_that_budget_used_ignores(
    tmp_path: Path,
) -> None:
    # Given: a fresh installation with a pinned UTC budget day.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        '[attention]\ntimezone = "UTC"\n',
        encoding="utf-8",
    )
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(paths.database, clock=clock) as store:
        empty = status_response(store, clock, paths)

        # When: a critical claim is recorded, then a routine claim crosses midnight.
        _ = store.situations.upsert_detections(
            (pending_detection("critical", "critical"),)
        )
        _ = store.situations.mark_delivered((store.situations.list_situations()[0].id,))
        after_critical = status_response(store, clock, paths)
        _ = store.situations.upsert_detections((pending_detection("routine"),))
        routine_id = next(
            item.id
            for item in store.situations.list_situations()
            if item.state == "pending"
        )
        _ = store.situations.mark_delivered((routine_id,))
        after_routine = status_response(store, clock, paths)
        clock.advance(timedelta(days=1))
        after_midnight = status_response(store, clock, paths)

    # Then: deliveries.total tracks every immutable event; budget.used does not.
    assert empty.deliveries.model_dump() == {"total": 0}
    assert empty.budget.used == 0
    assert after_critical.deliveries.model_dump() == {"total": 1}
    assert after_critical.budget.used == 0
    assert after_routine.deliveries.model_dump() == {"total": 2}
    assert after_routine.budget.used == 1
    assert after_midnight.deliveries.model_dump() == {"total": 2}
    assert after_midnight.budget.used == 0
    assert UNTRUSTED_SUBJECT not in after_midnight.model_dump_json()


def test_status_never_started_falls_back_to_configured_cadence(
    tmp_path: Path,
) -> None:
    # Given: a non-default poll interval and no daemon start record.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        "[daemon]\npoll_interval_minutes = 60\n",
        encoding="utf-8",
    )
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))

    # When: status is built before any watcher has claimed the row.
    with Store(paths.database, clock=clock) as store:
        status = status_response(store, clock, paths)

    # Then: missing persisted cadence is never-started, not an implied stale beat.
    assert load_config(paths.config).daemon.poll_interval == timedelta(minutes=60)
    assert status.daemon.liveness == "never_started"
    assert status.daemon.status == "not_running"
    assert any("never run" in warning for warning in status.warnings)
