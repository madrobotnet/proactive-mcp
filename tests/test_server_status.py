import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

from proactive_mcp.server import StatusResponse, build_status
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock


@dataclass(frozen=True, slots=True)
class FixedClock:
    now_value: datetime

    def now(self) -> datetime:
        return self.now_value


@pytest.mark.anyio
async def test_get_status_over_stdio(tmp_path: Path) -> None:
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
        result = await session.call_tool("get_status")

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
    assert status.overall == "degraded"
    assert status.warnings
    assert "all-clear" not in status.model_dump_json().lower()


def test_status_reports_stale_and_reauthentication_required_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an expired Gmail success and a shared Google grant that needs consent.
    database_path = tmp_path / "status.db"
    clock: Clock = FixedClock(datetime(2000, 1, 1, tzinfo=UTC))
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
    assert status.google.calendar.status == "needs_reauth"
    assert status.google.calendar.error_code == "invalid_grant"
    assert status.overall == "degraded"
    assert status.warnings
    assert "private-cursor" not in status.model_dump_json()
