from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from proactive_mcp.store import (
    DEFAULT_STALE_AFTER,
    SourceFreshness,
    SourceSyncState,
    Store,
    evaluate_source_freshness,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.clock import Clock


class FixedClock:
    """Mutable fake clock for deterministic transition tests."""

    now_value: datetime

    def __init__(self, now_value: datetime) -> None:
        self.now_value = now_value

    def now(self) -> datetime:
        return self.now_value

    def set(self, now: datetime) -> None:
        self.now_value = now


def test_sources_are_not_configured_before_google_authorization(tmp_path: Path) -> None:
    # Given: a newly migrated store.
    clock: Clock = FixedClock(datetime(2026, 8, 21, 9, 0, tzinfo=UTC))

    # When: no Google authorization state has been persisted.
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        states = store.list_source_sync()
        store.set_source_auth("gmail", "configured")
        gmail_state = store.get_source_sync("gmail")

    # Then: both supported sources are explicitly not configured before setup.
    assert states == (
        SourceSyncState(
            source="gmail",
            auth_state="not_configured",
            last_success_at=None,
            last_attempt_at=None,
            last_error_code=None,
            sync_cursor=None,
            updated_at=None,
        ),
        SourceSyncState(
            source="calendar",
            auth_state="not_configured",
            last_success_at=None,
            last_attempt_at=None,
            last_error_code=None,
            sync_cursor=None,
            updated_at=None,
        ),
    )
    assert evaluate_source_freshness(gmail_state, clock.now()).status == "never_synced"


def test_source_freshness_transitions_are_persisted_with_the_injected_clock(
    tmp_path: Path,
) -> None:
    # Given: a configured Google grant and a deterministic clock.
    synced_at = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    clock = FixedClock(synced_at)
    database_path = tmp_path / "proactive.db"

    # When: Gmail succeeds and Calendar fails on its first sync attempt.
    with Store(database_path, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail", sync_cursor="cursor-1")
        clock.set(synced_at + timedelta(minutes=1))
        store.record_sync_failure("calendar", error_code="network")
        gmail_state = store.get_source_sync("gmail")
        calendar_state = store.get_source_sync("calendar")

    # Then: success and error state retain deterministic, source-specific timestamps.
    assert gmail_state == SourceSyncState(
        source="gmail",
        auth_state="configured",
        last_success_at=synced_at,
        last_attempt_at=synced_at,
        last_error_code=None,
        sync_cursor="cursor-1",
        updated_at=synced_at,
    )
    assert calendar_state == SourceSyncState(
        source="calendar",
        auth_state="configured",
        last_success_at=None,
        last_attempt_at=synced_at + timedelta(minutes=1),
        last_error_code="network",
        sync_cursor=None,
        updated_at=synced_at + timedelta(minutes=1),
    )

    assert evaluate_source_freshness(gmail_state, synced_at) == SourceFreshness(
        status="ok",
        last_success_at=synced_at,
        last_attempt_at=synced_at,
        age_seconds=0,
        error_code=None,
    )
    assert (
        evaluate_source_freshness(
            gmail_state,
            synced_at + DEFAULT_STALE_AFTER + timedelta(seconds=1),
        ).status
        == "stale"
    )
    assert evaluate_source_freshness(calendar_state, clock.now()).status == "error"

    with Store(database_path, clock=clock) as reopened:
        assert reopened.list_source_sync() == (gmail_state, calendar_state)


def test_invalid_grant_requires_reauthorization_for_the_shared_google_grant(
    tmp_path: Path,
) -> None:
    # Given: a previous Gmail sync and an active shared Google grant.
    synced_at = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    revoked_at = synced_at + timedelta(days=1)
    clock = FixedClock(synced_at)

    # When: refresh reports invalid_grant for that shared grant.
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail", sync_cursor="cursor-1")
        clock.set(revoked_at)
        store.record_google_invalid_grant()
        states = store.list_source_sync()

    # Then: both sources need reauthorization while prior source data remains intact.
    gmail_state, calendar_state = states
    assert gmail_state.auth_state == "needs_reauth"
    assert gmail_state.last_success_at == synced_at
    assert gmail_state.sync_cursor == "cursor-1"
    assert gmail_state.last_attempt_at == revoked_at
    assert gmail_state.last_error_code == "invalid_grant"
    assert calendar_state.auth_state == "needs_reauth"
    assert calendar_state.last_success_at is None
    assert calendar_state.last_attempt_at == revoked_at
    assert calendar_state.last_error_code == "invalid_grant"
    assert evaluate_source_freshness(gmail_state, revoked_at).status == "needs_reauth"
