from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_tools import open_situation_service
from proactive_mcp.sources.lazy_sync import LazySyncPolicy, SourceAccess
from proactive_mcp.store import DaemonStatus, DaemonStatusStore, Store
from tests.daemon_cli_test_support import start_live_overridden_watcher
from tests.daemon_test_support import (
    FakeCredential,
    FakeCredentialStore,
    FakeReaderFactory,
    StoreBackedReader,
)
from tests.situation_test_support import FakeClock, utc_datetime
from tests.situation_tool_support import open_harness

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from proactive_mcp.clock import Clock

_NOON = utc_datetime(2026, 8, 21, 12)


def test_proactive_check_never_reports_all_clear_while_a_source_is_not_ok(
    tmp_path: Path,
) -> None:
    # Given: an installation whose Google setup never ran.
    with open_harness(tmp_path, _NOON) as harness:
        # When: the agent checks and no situation exists.
        response = harness.service.proactive_check()

    # Then: the empty result is never presented as an all-clear (§7).
    assert response.situations == ()
    assert response.all_clear is False
    assert response.freshness.gmail.status == "not_configured"
    assert response.freshness.calendar.status == "not_configured"
    assert "gmail: source is not_configured" in response.warnings
    assert "calendar: source is not_configured" in response.warnings


def test_proactive_check_reports_all_clear_only_when_no_source_warns(
    tmp_path: Path,
) -> None:
    # Given: both sources synced successfully inside the freshness window.
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        harness.store.set_google_auth_state("configured")
        harness.store.record_sync_success("gmail")
        harness.store.record_sync_success("calendar")

        # When: the agent checks with nothing detected.
        response = harness.service.proactive_check()

    # Then: an honest all-clear is allowed and nothing is held back.
    assert response.freshness.gmail.status == "ok"
    assert response.freshness.calendar.status == "ok"
    assert response.warnings == ()
    assert response.all_clear is True
    assert response.held_count == 0


def test_proactive_check_does_not_inline_read_a_sixty_minute_override_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: configured sources and a still-running daemon started at 60 minutes.
    paths, clock = start_live_overridden_watcher(tmp_path, monkeypatch)
    clock.advance(timedelta(minutes=16))
    seen: list[timedelta | None] = []
    original_status = DaemonStatusStore.status

    def capture_status(
        self: DaemonStatusStore, *, stale_after: timedelta | None = None
    ) -> DaemonStatus:
        seen.append(stale_after)
        return original_status(self, stale_after=stale_after)

    monkeypatch.setattr(DaemonStatusStore, "status", capture_status)
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        reader = StoreBackedReader(store=store)

        def open_access(
            _paths: ProactivePaths, bound: Store, _clock: Clock
        ) -> SourceAccess:
            return SourceAccess(
                sync_state=bound,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )

        monkeypatch.setattr(
            "proactive_mcp.server.situation_tools.open_source_access",
            open_access,
        )
        service = open_situation_service(store, clock, paths)

        # When: proactive_check evaluates lazy-sync 16 minutes after the beat.
        _ = service.proactive_check()

    # Then: liveness uses the 60-minute cadence, so no duplicate inline read.
    assert seen == [
        None,
        LazySyncPolicy.for_poll_interval(timedelta(minutes=60)).daemon_stale_after,
    ]
    assert reader.reads == []


def test_never_started_lazy_sync_uses_configured_poll_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 7-minute config and no daemon start record.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text(
        "[daemon]\npoll_interval_minutes = 7\n",
        encoding="utf-8",
    )
    clock = FakeClock(_NOON)
    seen: list[timedelta | None] = []
    original_status = DaemonStatusStore.status

    def capture_status(
        self: DaemonStatusStore, *, stale_after: timedelta | None = None
    ) -> DaemonStatus:
        seen.append(stale_after)
        return original_status(self, stale_after=stale_after)

    monkeypatch.setattr(DaemonStatusStore, "status", capture_status)
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        reader = StoreBackedReader(store=store)

        def open_access(
            _paths: ProactivePaths, bound: Store, _clock: Clock
        ) -> SourceAccess:
            return SourceAccess(
                sync_state=bound,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )

        monkeypatch.setattr(
            "proactive_mcp.server.situation_tools.open_source_access",
            open_access,
        )
        service = open_situation_service(store, clock, paths)

        # When: proactive_check evaluates lazy-sync with no persisted cadence.
        persisted = store.daemon.status().poll_interval
        _ = service.proactive_check()

    # Then: the configured interval is the never-started liveness fallback.
    assert persisted is None
    assert seen == [
        None,
        None,
        LazySyncPolicy.for_poll_interval(timedelta(minutes=7)).daemon_stale_after,
    ]
    assert reader.reads == [1]
