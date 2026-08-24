from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from proactive_mcp.sources.gmail import (
    GMAIL_PROFILE_URL,
    GMAIL_THREADS_URL,
    GmailAdapter,
    GmailHttpResponse,
)

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
    assert snapshot.is_complete is False
    assert snapshot.latest_from_user is False
    assert snapshot.user_is_recipient is False
    assert snapshot.degradation_reasons == ("direction_metadata_ambiguous",)
    assert THREAD_ID in result.resolution_excluded_thread_ids
    assert THREAD_ID not in result.resolution_safe_thread_ids
