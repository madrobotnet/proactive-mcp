from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest

from proactive_mcp.situations import SituationEngine
from proactive_mcp.sources.calendar import CalendarReadResult
from proactive_mcp.sources.gmail import (
    GMAIL_PROFILE_URL,
    GMAIL_THREADS_URL,
    GmailAdapter,
    GmailHttpResponse,
)
from proactive_mcp.sources.google_sync import GoogleReadDependencies, GoogleSyncService
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping

    from proactive_mcp.clock import Clock

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "gmail"
NOW = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
THREAD_ID = "thread-deadline"


class FixedClock:
    _now: datetime

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeCalendarReader:
    def list_events(self) -> CalendarReadResult:
        return CalendarReadResult(
            events=(),
            fetched_at=NOW.isoformat(),
            window_start=NOW.isoformat(),
            window_end=NOW.isoformat(),
            page_count=1,
            skipped_count=0,
        )


class FakeCredentials:
    def delete(self) -> None:
        raise AssertionError


class FakeGmailTransport:
    """Map URL plus page token to fixture bytes without opening a socket."""

    _responses: dict[str, dict[str | None, tuple[int, bytes]]]

    def __init__(
        self,
        responses: Mapping[str, Mapping[str | None, tuple[int, bytes]]],
    ) -> None:
        self._responses = {url: dict(pages) for url, pages in responses.items()}

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: Mapping[str, str],
    ) -> GmailHttpResponse:
        del method
        page_token = query.get("pageToken")
        status_code, body = self._responses[url][page_token]
        return GmailHttpResponse(status_code=status_code, body=body)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_neutral_labels_are_ambiguous_and_not_resolution_safe() -> None:
    # Given: a real fixture thread whose latest message has labels, but neither
    # INBOX nor SENT. STARRED is a valid non-empty Gmail label that does not
    # decide direction.
    thread_url = f"{GMAIL_THREADS_URL}/{THREAD_ID}"
    starred_latest = _fixture("thread_deadline.json").replace(
        b'"labelIds": ["INBOX"]',
        b'"labelIds": ["STARRED"]',
    )
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_deadline.json"))},
            thread_url: {None: (200, starred_latest)},
        }
    )
    clock: Clock = FixedClock(NOW)

    # When: the production adapter projects the thread for detector resolution.
    result = GmailAdapter(transport, clock).read_inbox_threads()

    # Then: direction is incomplete and the thread cannot be resolution-safe.
    assert len(result.threads) == 1
    snapshot = result.threads[0]
    assert snapshot.thread_id == THREAD_ID
    assert snapshot.latest_message_id == "message-z"
    assert snapshot.resolution_safe is False
    assert snapshot.latest_from_user is False
    assert snapshot.user_is_recipient is False
    assert snapshot.degradation_reasons == ("direction_metadata_ambiguous",)
    assert THREAD_ID in result.resolution_excluded_thread_ids
    assert THREAD_ID not in result.resolution_safe_thread_ids


def test_partial_thread_warning_does_not_degrade_successful_source_read(
    tmp_path: Path,
) -> None:
    # Given: Gmail successfully returns one neutral-label thread whose projection
    # is warning-bearing and unsafe for resolution.
    thread_url = f"{GMAIL_THREADS_URL}/{THREAD_ID}"
    starred_latest = _fixture("thread_deadline.json").replace(
        b'"labelIds": ["INBOX"]',
        b'"labelIds": ["STARRED"]',
    )
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_deadline.json"))},
            thread_url: {None: (200, starred_latest)},
        }
    )
    clock: Clock = FixedClock(NOW)

    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=GmailAdapter(transport, clock),
                calendar=FakeCalendarReader(),
                credentials=FakeCredentials(),
            )
        )

        # When: the same result crosses the legacy smoke/sync surface and the
        # daemon's evaluation preparation and persistence path.
        smoke = service.read_smoke(enabled=True)
        prepared = service.prepare_evaluation()
        evaluated = SituationEngine(store, clock, UTC).evaluate(prepared)
        sync_state = store.get_source_sync("gmail")
        generation_state = store.source_generation_state("gmail")

    # Then: provider freshness is successful, while the warning and unsafe
    # resolution scope remain explicit through evaluation.
    assert smoke.gmail_count == 1
    assert smoke.gmail_error_code is None
    assert sync_state.last_error_code is None
    assert sync_state.last_success_at is not None
    assert prepared.gmail_threads is not None
    assert prepared.gmail_threads.complete is True
    assert prepared.gmail_threads.warning_codes == ("direction_metadata_ambiguous",)
    assert prepared.gmail_threads.resolution_scope_ids == frozenset()
    assert prepared.gmail_threads.resolution_excluded_ids == frozenset({THREAD_ID})
    assert evaluated.gmail_freshness.status == "ok"
    assert "gmail: direction_metadata_ambiguous" in evaluated.warnings
    assert generation_state.status == "complete"


@pytest.mark.parametrize(
    ("detail", "reason", "projected_count"),
    [
        pytest.param(
            json.dumps(
                {
                    "messages": [
                        {
                            "id": "message",
                            "internalDate": "1787302800000",
                            "labelIds": ["INBOX"],
                            "payload": {
                                "mimeType": "text/plain",
                                "body": {
                                    "data": base64.urlsafe_b64encode(
                                        b"x" * 5_000
                                    ).decode()
                                },
                            },
                        }
                    ]
                }
            ).encode(),
            "body_truncated",
            1,
            id="body-truncated",
        ),
        pytest.param(
            json.dumps(
                {
                    "messages": [
                        {
                            "id": "message",
                            "internalDate": "1787302800000",
                            "labelIds": ["INBOX"],
                            "snippet": "bounded preview",
                        }
                    ]
                }
            ).encode(),
            "body_snippet_fallback",
            1,
            id="snippet-fallback",
        ),
        pytest.param(
            b'{"messages":[{"id":"message","labelIds":["INBOX"]}]}',
            "thread_without_projectable_message",
            0,
            id="unprojectable",
        ),
    ],
)
def test_safe_projection_warning_preserves_source_health_and_excludes_resolution(
    tmp_path: Path,
    detail: bytes,
    reason: str,
    projected_count: int,
) -> None:
    # Given: provider coverage is complete but one thread projection is unsafe.
    thread_id = "thread-warning"
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {
                None: (200, json.dumps({"threads": [{"id": thread_id}]}).encode())
            },
            f"{GMAIL_THREADS_URL}/{thread_id}": {None: (200, detail)},
        }
    )
    clock: Clock = FixedClock(NOW)
    with Store(tmp_path / "proactive.db") as store:
        service = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=GmailAdapter(transport, clock),
                calendar=FakeCalendarReader(),
                credentials=FakeCredentials(),
            )
        )

        # When: the warning-bearing result crosses sync and evaluation preparation.
        summary = service.sync()
        prepared = service.prepare_evaluation()
        sync_state = store.get_source_sync("gmail")

    # Then: source freshness stays healthy and the unsafe thread cannot resolve.
    assert summary.gmail_count == projected_count
    assert summary.gmail_error_code is None
    assert sync_state.last_error_code is None
    assert prepared.gmail_threads is not None
    assert prepared.gmail_threads.complete is True
    assert prepared.gmail_threads.warning_codes == (reason,)
    assert prepared.gmail_threads.resolution_scope_ids == frozenset()
    assert prepared.gmail_threads.resolution_excluded_ids == frozenset({thread_id})
