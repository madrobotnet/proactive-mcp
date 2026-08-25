from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

import pytest

from proactive_mcp.server.situation_responses import source_read_diagnostics_response
from proactive_mcp.sources.google_sync import GoogleReadDependencies, GoogleSyncService
from proactive_mcp.store import Store
from proactive_mcp.store.sync import (
    SourceReadReason,
    SourceReadReasonCount,
    source_failure_diagnostics,
)
from tests.google_sync_test_support import (
    FakeCalendarReader,
    FakeCredentials,
    FakeInboxReader,
    calendar_result,
    gmail_inbox_result,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.sources.gmail import GmailDegradationReason


def test_direct_sync_atomically_persists_pii_free_gmail_diagnostics(
    tmp_path: Path,
) -> None:
    # Given: a bounded read with nonzero counters and PII-shaped source values.
    database = tmp_path / "proactive.db"
    canaries = (
        "person@example.test",
        "SELECT secret FROM private_mail",
        "/home/person/mail.eml",
    )
    result = replace(
        gmail_inbox_result(),
        threads=(
            replace(
                gmail_inbox_result().threads[0],
                thread_id=canaries[0],
                latest_message_id=canaries[1],
                subject=canaries[2],
            ),
        ),
        provider_history_cursor=None,
        request_count=7,
        page_count=3,
        projected_thread_count=1,
        excluded_thread_count=2,
        degradation_reasons=("body_truncated",),
        degradation_reason_counts=(("body_truncated", 2),),
    )
    with Store(database) as store:
        summary = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(result),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        ).sync()
        persisted = store.gmail_diagnostics()
        gmail_state = store.get_source_sync("gmail")

    # When: the database is closed and opened by a new process boundary.
    with Store(database) as reopened:
        reopened_diagnostics = reopened.gmail_diagnostics()
        dump = "\n".join(reopened.connection().iterdump())

    # Then: freshness and only closed aggregate diagnostics committed together.
    assert gmail_state.last_success_at is not None
    assert persisted == summary.gmail_diagnostics
    assert reopened_diagnostics == summary.gmail_diagnostics
    assert reopened_diagnostics is not None
    assert reopened_diagnostics.request_count == 7
    assert reopened_diagnostics.reason_counts[0].count == 2
    assert all(canary not in dump for canary in canaries)


def test_store_rejects_unclosed_diagnostic_reason_without_persisting_it(
    tmp_path: Path,
) -> None:
    # Given: an untrusted string is forced across the typed store boundary.
    canary = "person@example.test /private/mail SELECT token"
    malformed = replace(
        source_failure_diagnostics("network"),
        reason_counts=(
            SourceReadReasonCount(cast("SourceReadReason", cast("object", canary)), 1),
        ),
    )
    database = tmp_path / "proactive.db"
    with Store(database) as store:
        with pytest.raises(ValueError, match="invalid Gmail diagnostics"):
            store.record_gmail_sync(malformed, error_code="network")
        assert store.gmail_diagnostics() is None
        assert store.get_source_sync("gmail").last_attempt_at is None
        dump = "\n".join(store.connection().iterdump())

    # Then: neither the malformed reason nor partial freshness reaches disk.
    assert canary not in dump


@pytest.mark.parametrize(
    ("coverage_complete", "reason", "expected_outcome"),
    [
        (True, "body_truncated", "healthy"),
        (False, "pagination_limit", "partial"),
        (False, "sync_budget_exhausted", "partial"),
        (False, "thread_projection_limit", "partial"),
        (False, "thread_response_too_large", "partial"),
    ],
)
def test_sync_distinguishes_safe_warnings_from_true_read_bounds(
    tmp_path: Path,
    coverage_complete: bool,
    reason: GmailDegradationReason,
    expected_outcome: Literal["healthy", "partial"],
) -> None:
    # Given: one Gmail read carrying either a safe warning or a true bound.
    gmail_result = replace(
        gmail_inbox_result(),
        coverage_complete=coverage_complete,
        degradation_reasons=(reason,),
        request_count=4,
        page_count=2,
        projected_thread_count=1,
        excluded_thread_count=1,
    )
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(gmail_result),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        )

        # When: the typed synchronization surface summarizes the read.
        summary = service.sync()

    # Then: safe projection warnings stay healthy and true bounds stay partial.
    diagnostics = summary.gmail_diagnostics
    assert diagnostics.outcome == expected_outcome
    assert diagnostics.request_count == 4
    assert diagnostics.page_count == 2
    assert diagnostics.projected_count == 1
    assert diagnostics.excluded_count == 1
    assert diagnostics.byte_budget == 8_000_000
    assert {item.reason: item.count for item in diagnostics.reason_counts} == {
        reason: 1
    }


def test_sync_preserves_repeated_reason_counts_in_serialized_diagnostics(
    tmp_path: Path,
) -> None:
    # Given: two projected threads carry the same safe degradation reason.
    base = gmail_inbox_result()
    first = replace(base.threads[0], degradation_reasons=("body_truncated",))
    second = replace(
        first,
        thread_id="thread-2",
        latest_message_id="message-2",
    )
    repeated = replace(
        base,
        threads=(first, second),
        projected_thread_count=2,
        excluded_thread_count=2,
        degradation_reasons=("body_truncated",),
        degradation_reason_counts=(("body_truncated", 2),),
    )
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(repeated),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        )

        # When: sync and the shared smoke/daemon serializer summarize the read.
        summary = service.sync()
        serialized = source_read_diagnostics_response(summary.gmail_diagnostics)

    # Then: the unique legacy reason still reports both occurrences in the map.
    assert summary.gmail_diagnostics.reason_counts[0].count == 2
    assert serialized.reason_counts == {"body_truncated": 2}


def test_sync_does_not_record_degraded_projection_as_fresh(tmp_path: Path) -> None:
    degraded_gmail = replace(
        gmail_inbox_result(),
        coverage_complete=False,
        degradation_reasons=("pagination_limit",),
    )
    degraded_calendar = replace(calendar_result(), skipped_count=1)
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(degraded_gmail),
                calendar=FakeCalendarReader(degraded_calendar),
                credentials=FakeCredentials(),
            )
        )

        summary = service.sync()
        gmail_state, calendar_state = store.list_source_sync()

    assert summary.gmail_error_code == "degraded"
    assert summary.calendar_error_code == "degraded"
    assert gmail_state.last_success_at is None
    assert calendar_state.last_success_at is None
    assert gmail_state.last_error_code == "degraded"
    assert calendar_state.last_error_code == "degraded"
