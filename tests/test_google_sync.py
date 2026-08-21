from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from proactive_mcp import sources
from proactive_mcp.situations.inputs import InboxThreadSnapshot
from proactive_mcp.sources.calendar import CalendarEvent, CalendarReadResult
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStorageError,
    GoogleCredential,
)
from proactive_mcp.sources.gmail import GmailError, GmailInboxReadResult
from proactive_mcp.sources.google_sync import (
    GoogleReadDependencies,
    GoogleReadSmokeDisabledError,
    GoogleReadSummary,
    GoogleSyncService,
    GoogleTransportError,
    InvalidGrantError,
)
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from pathlib import Path


NOW = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FailingGmailReader:
    error: GmailError | GoogleTransportError | InvalidGrantError

    def read_inbox_threads(self) -> GmailInboxReadResult:
        raise self.error


@dataclass(frozen=True, slots=True)
class FakeInboxReader:
    result: GmailInboxReadResult

    def read_inbox_threads(self) -> GmailInboxReadResult:
        return self.result


@dataclass(frozen=True, slots=True)
class FakeCalendarReader:
    result: CalendarReadResult

    def list_events(self) -> CalendarReadResult:
        return self.result


@dataclass(frozen=True, slots=True)
class FakeCredentials:
    """Record credential deletion as the observable fake behavior."""

    delete_calls: list[bool] = field(default_factory=list)
    delete_error: CredentialStorageError | None = None

    @property
    def refresh_token(self) -> str:
        """Provide a non-secret durable token marker for the OAuth fake."""
        return "redacted"

    @property
    def scopes(self) -> tuple[str, str]:
        """Provide the fixed read-only scopes required by the credential contract."""
        return GOOGLE_READONLY_SCOPES

    def to_json(self) -> str:
        """Provide an inert serialization because this fake is never persisted."""
        return "{}"

    def delete(self) -> None:
        self.delete_calls.append(True)
        if self.delete_error is not None:
            raise self.delete_error


def _calendar_result() -> CalendarReadResult:
    return CalendarReadResult(
        events=(
            CalendarEvent(
                id="event-1",
                status="confirmed",
                summary="Private planning event",
                start=None,
                end=None,
                is_organizer=True,
                self_response_status="accepted",
            ),
        ),
        fetched_at=NOW.isoformat(),
        window_start=NOW.isoformat(),
        window_end=NOW.isoformat(),
        page_count=1,
        skipped_count=0,
    )


def _gmail_inbox_result() -> GmailInboxReadResult:
    thread = InboxThreadSnapshot(
        thread_id="thread-1",
        latest_message_id="message-1",
        latest_from_user=False,
        user_is_recipient=True,
        latest_message_at=NOW,
    )
    return GmailInboxReadResult(
        threads=(thread,),
        fetched_at=NOW.isoformat(),
        provider_history_cursor="history-2",
        page_count=1,
        is_complete=True,
        degradation_reasons=(),
    )


def test_sync_prepares_gmail_projection_without_recording_success(
    tmp_path: Path,
) -> None:
    # Given: a complete Gmail projection ready for later engine application.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(_gmail_inbox_result()),
                calendar=FakeCalendarReader(_calendar_result()),
                credentials=credentials,
            )
        )

        # When: synchronization prepares the source read.
        summary = service.sync()
        gmail_state = store.get_source_sync("gmail")

    # Then: snapshot identity is returned but freshness awaits engine application.
    assert summary.gmail_count == 1
    assert summary.gmail_ids == ("thread-1",)
    assert gmail_state.last_success_at is None
    assert gmail_state.sync_cursor is None


def test_prepare_evaluation_reserves_ordered_detector_snapshots(
    tmp_path: Path,
) -> None:
    # Given: complete Gmail and Calendar read projections.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(_gmail_inbox_result()),
                calendar=FakeCalendarReader(_calendar_result()),
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

    monkeypatch.setattr(sources.GoogleOAuthAuthorizer, "authorize", authorize)

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


def test_sync_updates_each_source_independently_and_returns_redacted_values_only(
    tmp_path: Path,
) -> None:
    # Given: a stale Gmail success fails while Calendar has a successful read.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        store.record_sync_success("gmail", sync_cursor="prior-cursor")
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FailingGmailReader(GmailError(error_code="http_4xx")),
                calendar=FakeCalendarReader(_calendar_result()),
                credentials=credentials,
            )
        )

        # When: the service runs its M2 read-only synchronization.
        summary = service.sync()
        gmail_state = store.get_source_sync("gmail")
        calendar_state = store.get_source_sync("calendar")

    # Then: Calendar is fresh, Gmail's error is persisted, and PII is absent.
    assert summary == GoogleReadSummary(
        gmail_count=0,
        gmail_ids=(),
        gmail_error_code="http_4xx",
        calendar_count=1,
        calendar_ids=("event-1",),
        calendar_error_code=None,
    )
    assert gmail_state.last_error_code == "http_4xx"
    assert gmail_state.last_success_at is not None
    assert gmail_state.sync_cursor == "prior-cursor"
    assert calendar_state.last_error_code is None
    assert calendar_state.last_success_at is not None
    assert credentials.delete_calls == []
    assert "user@example.test" not in repr(summary)
    assert "Private planning event" not in repr(summary)
    assert "history-1" not in repr(summary)


def test_sync_persists_normalized_transport_failure(tmp_path: Path) -> None:
    # Given: Gmail times out while Calendar remains readable.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FailingGmailReader(GoogleTransportError("timeout")),
                calendar=FakeCalendarReader(_calendar_result()),
                credentials=credentials,
            )
        )

        # When: the read service crosses both source boundaries.
        summary = service.sync()
        gmail_state = store.get_source_sync("gmail")

    # Then: the timeout is persisted instead of leaving a false fresh status.
    assert summary.gmail_error_code == "timeout"
    assert gmail_state.last_error_code == "timeout"


def test_invalid_grant_requires_reauthorization_for_both_sources(
    tmp_path: Path,
) -> None:
    # Given: Gmail's shared OAuth grant has been revoked.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FailingGmailReader(InvalidGrantError()),
                calendar=FakeCalendarReader(_calendar_result()),
                credentials=credentials,
            )
        )

        # When: the read service sees the invalid_grant outcome.
        summary = service.sync()
        gmail_state, calendar_state = store.list_source_sync()

    # Then: credentials are cleared, both sources require reauthorization, and
    # Calendar is not attempted with the revoked shared grant.
    assert summary.gmail_error_code == "invalid_grant"
    assert summary.calendar_error_code == "invalid_grant"
    assert summary.gmail_count == 0
    assert summary.calendar_count == 0
    assert credentials.delete_calls == [True]
    assert gmail_state.auth_state == "needs_reauth"
    assert gmail_state.last_error_code == "invalid_grant"
    assert calendar_state.auth_state == "needs_reauth"
    assert calendar_state.last_error_code == "invalid_grant"


def test_invalid_grant_state_survives_credential_deletion_failure(
    tmp_path: Path,
) -> None:
    # Given: Google revoked the grant and secure credential deletion is unavailable.
    credentials = FakeCredentials(delete_error=CredentialStorageError())
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FailingGmailReader(InvalidGrantError()),
                calendar=FakeCalendarReader(_calendar_result()),
                credentials=credentials,
            )
        )

        # When: invalid_grant handling cannot delete the persisted credential.
        summary = service.sync()
        gmail_state, calendar_state = store.list_source_sync()

    # Then: both sources still require reauthorization with redacted outcomes.
    assert summary.gmail_error_code == "invalid_grant"
    assert summary.calendar_error_code == "invalid_grant"
    assert gmail_state.auth_state == "needs_reauth"
    assert calendar_state.auth_state == "needs_reauth"
    assert credentials.delete_calls == [True]


def test_real_account_read_is_explicitly_opt_in(tmp_path: Path) -> None:
    # Given: authenticated readers that would otherwise perform real reads.
    credentials = FakeCredentials()
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(_gmail_inbox_result()),
                calendar=FakeCalendarReader(_calendar_result()),
                credentials=credentials,
            )
        )

        # When: the smoke operation is invoked without opt-in.
        with pytest.raises(GoogleReadSmokeDisabledError):
            _ = service.read_smoke(enabled=False)

        # Then: no source state or credential is modified.
        assert store.list_source_sync()[0].last_attempt_at is None
        assert store.list_source_sync()[1].last_attempt_at is None
        assert credentials.delete_calls == []
