from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from proactive_mcp.sources.credentials import CredentialStorageError
from proactive_mcp.sources.gmail import GmailError
from proactive_mcp.sources.google_sync import (
    GoogleReadDependencies,
    GoogleReadSmokeDisabledError,
    GoogleSyncService,
    GoogleTransportError,
    InvalidGrantError,
)
from proactive_mcp.store import Store
from tests.google_sync_test_support import (
    FailingGmailReader,
    FakeCalendarReader,
    FakeCredentials,
    FakeInboxReader,
    calendar_result,
    gmail_inbox_result,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_sync_records_successful_gmail_projection_and_cursor(
    tmp_path: Path,
) -> None:
    # Given: a complete Gmail projection for the legacy sync/read-smoke surface.
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

        # When: the legacy synchronization surface completes.
        summary = service.sync()
        gmail_state = store.get_source_sync("gmail")

    # Then: snapshot identity and freshness preserve the public sync contract.
    assert summary.gmail_count == 1
    assert summary.gmail_ids == ("thread-1",)
    assert gmail_state.last_success_at is not None
    assert gmail_state.sync_cursor == "history-2"


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
                calendar=FakeCalendarReader(calendar_result()),
                credentials=credentials,
            )
        )

        # When: the service runs its M2 read-only synchronization.
        summary = service.sync()
        gmail_state = store.get_source_sync("gmail")
        calendar_state = store.get_source_sync("calendar")

    # Then: Calendar is fresh, Gmail's error is persisted, and PII is absent.
    assert (
        summary.gmail_count,
        summary.gmail_ids,
        summary.gmail_error_code,
        summary.calendar_count,
        summary.calendar_ids,
        summary.calendar_error_code,
    ) == (0, (), "http_4xx", 1, ("event-1",), None)
    assert summary.gmail_diagnostics.outcome == "auth_error"
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
                calendar=FakeCalendarReader(calendar_result()),
                credentials=credentials,
            )
        )

        # When: the read service crosses both source boundaries.
        summary = service.sync()
        gmail_state = store.get_source_sync("gmail")

    # Then: the timeout is persisted instead of leaving a false fresh status.
    assert summary.gmail_error_code == "timeout"
    assert summary.gmail_diagnostics.outcome == "transport_error"
    assert {
        item.reason: item.count for item in summary.gmail_diagnostics.reason_counts
    } == {"timeout": 1}
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
                calendar=FakeCalendarReader(calendar_result()),
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
    assert summary.gmail_diagnostics.outcome == "auth_error"
    assert {
        item.reason: item.count for item in summary.gmail_diagnostics.reason_counts
    } == {"invalid_grant": 1}
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
                calendar=FakeCalendarReader(calendar_result()),
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
                gmail=FakeInboxReader(gmail_inbox_result()),
                calendar=FakeCalendarReader(calendar_result()),
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
