from __future__ import annotations

from typing import TYPE_CHECKING

from proactive_mcp import sources
from proactive_mcp.sources.google_sync import GoogleReadDependencies, GoogleSyncService
from proactive_mcp.store import Store
from tests.google_sync_test_support import (
    FakeCalendarReader,
    FakeCredentials,
    FakeInboxReader,
    calendar_result,
    gmail_inbox_result,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from proactive_mcp.sources.credentials import GoogleCredential


def test_sync_freshness_state_survives_close_and_reopen(tmp_path: Path) -> None:
    # Given: the direct sync surface records one complete Google read.
    database = tmp_path / "proactive.db"
    with Store(database) as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(gmail_inbox_result()),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        )
        _ = service.sync()

    # When: a new store instance reads the existing migration-9 source state.
    with Store(database) as reopened:
        gmail_state, calendar_state = reopened.list_source_sync()

    # Then: freshness and the Gmail cursor remain durable across connections.
    assert gmail_state.last_success_at is not None
    assert gmail_state.last_attempt_at == gmail_state.last_success_at
    assert gmail_state.sync_cursor == "history-2"
    assert calendar_state.last_success_at is not None
    assert calendar_state.last_attempt_at == calendar_state.last_success_at


def test_prepare_evaluation_reserves_ordered_detector_snapshots(
    tmp_path: Path,
) -> None:
    # Given: complete Gmail and Calendar read projections.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(gmail_inbox_result()),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=credentials,
            )
        )

        # When: source work is prepared for one atomic engine pass.
        prepared = service.prepare_evaluation()
        gmail_state = store.source_generation_state("gmail")
        calendar_state = store.source_generation_state("calendar")

    # Then: generation tickets and completeness travel with detector inputs.
    assert prepared.gmail_threads is not None
    assert prepared.gmail_threads.generation.source == "gmail"
    assert prepared.gmail_threads.generation.number == 1
    assert prepared.gmail_threads.items[0].thread_id == "thread-1"
    assert prepared.gmail_threads.sync_cursor == "history-2"
    assert prepared.gmail_threads.complete is True
    assert prepared.calendar_events is not None
    assert prepared.calendar_events.generation.source == "calendar"
    assert prepared.calendar_events.generation.number == 1
    assert prepared.calendar_events.items[0].id == "event-1"
    assert prepared.calendar_events.complete is True
    assert gmail_state.issued == 1
    assert gmail_state.applied == 0
    assert calendar_state.issued == 1
    assert calendar_state.applied == 0


def test_setup_runtime_marks_both_sources_configured_after_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an authorized credential returned by the existing OAuth capability.
    database_path = tmp_path / "state" / "proactive.db"
    client_secrets_path = tmp_path / "client_secret.json"
    _ = client_secrets_path.write_text("{}")

    def authorize(
        self: sources.GoogleOAuthAuthorizer,
        path: Path,
        *,
        reauth: bool,
        headless: bool,
    ) -> GoogleCredential:
        assert self.credential_store.state_directory == database_path.parent
        assert path == client_secrets_path
        assert reauth
        assert headless
        return FakeCredentials()

    success_states: list[tuple[str, str]] = []

    def emit_success() -> None:
        with Store(database_path) as persisted:
            gmail, calendar = persisted.list_source_sync()
        success_states.append((gmail.auth_state, calendar.auth_state))

    monkeypatch.setattr(sources.GoogleOAuthAuthorizer, "authorize", authorize)
    monkeypatch.setattr(sources, "write_headless_setup_success", emit_success)

    # When: the concrete setup runtime completes authorization.
    sources.configure_google_sources(
        database_path,
        sources.GoogleSetupOptions(
            client_secrets_path=client_secrets_path,
            reauth=True,
            headless=True,
        ),
    )
    with Store(database_path) as store:
        gmail_state, calendar_state = store.list_source_sync()

    # Then: both halves of the shared grant are configured before their first read.
    assert gmail_state.auth_state == "configured"
    assert calendar_state.auth_state == "configured"
    assert success_states == [("configured", "configured")]
