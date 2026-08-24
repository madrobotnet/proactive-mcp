from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from proactive_mcp import situations
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


class FixedClock:
    _now: datetime

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeGmailTransport:
    """Map URL plus page token to fixture bytes without opening a socket."""

    calls: list[tuple[str, str, dict[str, str]]]
    _responses: dict[str, dict[str | None, tuple[int, bytes]]]

    def __init__(
        self,
        responses: Mapping[str, Mapping[str | None, tuple[int, bytes]]] | None = None,
    ) -> None:
        self.calls = []
        self._responses = {url: dict(pages) for url, pages in (responses or {}).items()}

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: Mapping[str, str],
    ) -> GmailHttpResponse:
        self.calls.append((method, url, dict(query)))
        page_token = query.get("pageToken")
        status_code, body = self._responses[url][page_token]
        return GmailHttpResponse(status_code=status_code, body=body)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _adapter(transport: FakeGmailTransport) -> GmailAdapter:
    clock: Clock = FixedClock(NOW)
    return GmailAdapter(transport, clock)


def test_read_inbox_threads_projects_deadline_from_plain_text_body() -> None:
    # Given: an inbox thread whose deadline appears only in the latest MIME body.
    thread_url = f"{GMAIL_THREADS_URL}/thread-deadline"
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_deadline.json"))},
            thread_url: {None: (200, _fixture("thread_deadline.json"))},
        }
    )

    # When: the production Gmail adapter builds detector-ready snapshots.
    result = _adapter(transport).read_inbox_threads()

    # Then: deterministic latest-message fields include the decoded body deadline.
    assert len(result.threads) == 1
    snapshot = result.threads[0]
    assert snapshot.thread_id == "thread-deadline"
    assert snapshot.latest_message_id == "message-z"
    assert snapshot.latest_from_user is False
    assert snapshot.user_is_recipient is True
    assert snapshot.latest_message_at == datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    assert snapshot.subject == "Project update"
    assert snapshot.sender_display == "Fixture Sender"
    assert snapshot.snippet == "Please review the attached details."
    assert snapshot.body_text == "Please reply by 2026-08-22."
    assert "2026-08-22" not in snapshot.subject
    assert "2026-08-22" not in snapshot.snippet
    assert result.provider_history_cursor == "12345"
    assert result.is_complete is True
    assert result.degradation_reasons == ()
    assert [call[1] for call in transport.calls] == [
        GMAIL_PROFILE_URL,
        GMAIL_THREADS_URL,
        thread_url,
    ]
    assert transport.calls[2][2] == {"format": "full"}


def test_spoofed_from_header_cannot_impersonate_a_sent_message() -> None:
    thread_url = f"{GMAIL_THREADS_URL}/thread-deadline"
    spoofed = _fixture("thread_deadline.json").replace(
        b"Fixture Sender <sender@example.test>",
        b"User <USER@EXAMPLE.COM>",
    )
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_deadline.json"))},
            thread_url: {None: (200, spoofed)},
        }
    )

    snapshot = _adapter(transport).read_inbox_threads().threads[0]

    assert snapshot.latest_from_user is False
    assert snapshot.user_is_recipient is True


def test_missing_provider_direction_labels_degrade_without_header_fallback() -> None:
    thread_url = f"{GMAIL_THREADS_URL}/thread-deadline"
    missing_labels = (
        _fixture("thread_deadline.json")
        .replace(
            b'      "labelIds": ["INBOX"],\r\n',
            b"",
        )
        .replace(
            b'      "labelIds": ["INBOX"],\n',
            b"",
        )
    )
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_deadline.json"))},
            thread_url: {None: (200, missing_labels)},
        }
    )

    result = _adapter(transport).read_inbox_threads()

    assert result.threads[0].latest_from_user is False
    assert result.threads[0].user_is_recipient is False
    assert "direction_metadata_missing" in result.degradation_reasons
    assert result.is_complete is True
    assert result.resolution_safe_thread_ids == frozenset()


def test_read_inbox_threads_preserves_adjacent_html_block_deadline() -> None:
    # Given: the deadline phrase spans adjacent block elements in an HTML-only body.
    thread_url = f"{GMAIL_THREADS_URL}/thread-html-deadline"
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_html_deadline.json"))},
            thread_url: {None: (200, _fixture("thread_html_deadline.json"))},
        }
    )

    # When: the adapter projects the HTML-only message.
    result = _adapter(transport).read_inbox_threads()
    detected = situations.detect_reply_deadlines(
        result.threads,
        now=NOW,
        tz=UTC,
    )

    # Then: tags are removed and the body deadline reaches the detector.
    assert result.threads[0].body_text == "Please reply by 2026-08-22."
    assert result.is_complete is True
    assert len(detected) == 1
    assert detected[0].evidence.facts["deadline_date"] == "2026-08-22"


def test_read_inbox_threads_bounds_thread_count_and_body_text() -> None:
    # Given: more thread IDs than allowed and an oversized plain-text body.
    listed = json.dumps({"threads": [{"id": "first"}, {"id": "second"}]}).encode()
    oversized = "deadline " + "x" * 5_000
    encoded = base64.urlsafe_b64encode(oversized.encode()).decode().rstrip("=")
    detail = _fixture("thread_deadline.json").replace(
        b"UGxlYXNlIHJlcGx5IGJ5IDIwMjYtMDgtMjIu",
        encoded.encode(),
    )
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, listed)},
            f"{GMAIL_THREADS_URL}/first": {None: (200, detail)},
        }
    )
    adapter = GmailAdapter(
        transport,
        FixedClock(NOW),
        max_projected_threads=1,
    )

    # When: the detector-ready projection is read.
    result = adapter.read_inbox_threads()

    # Then: only one bounded body is retained and the generation is degraded.
    assert len(result.threads) == 1
    assert result.threads[0].body_text is not None
    assert len(result.threads[0].body_text) == 4_000
    assert result.is_complete is False
    assert result.degradation_reasons == (
        "thread_projection_limit",
        "body_truncated",
    )
    assert all("/second" not in call[1] for call in transport.calls)


def test_read_inbox_threads_marks_snippet_body_fallback_as_degraded() -> None:
    # Given: a full thread response without a MIME text/plain part.
    thread_url = f"{GMAIL_THREADS_URL}/thread-fallback"
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_fallback.json"))},
            thread_url: {None: (200, _fixture("thread_fallback.json"))},
        }
    )

    # When: the adapter projects the latest message.
    result = _adapter(transport).read_inbox_threads()

    # Then: the snippet remains warning-bearing and resolution-unsafe without
    # turning the successful provider read into a source coverage failure.
    snapshot = result.threads[0]
    assert snapshot.body_text == "Fallback preview"
    assert snapshot.is_complete is False
    assert snapshot.degradation_reasons == ("body_snippet_fallback",)
    assert snapshot.provider_history_cursor == "12345"
    assert result.is_complete is True
    assert result.degradation_reasons == ("body_snippet_fallback",)
    assert result.resolution_excluded_thread_ids == frozenset({"thread-fallback"})
