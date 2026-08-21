from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import pytest

from proactive_mcp import situations
from proactive_mcp.sources.gmail import (
    DEFAULT_MAX_PAGES,
    GMAIL_PROFILE_URL,
    GMAIL_THREADS_URL,
    GmailAdapter,
    GmailAuthError,
    GmailHttpResponse,
    GmailParseError,
    GmailThread,
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
    _repeating: bool

    def __init__(
        self,
        responses: Mapping[str, Mapping[str | None, tuple[int, bytes]]] | None = None,
        *,
        repeating: bool = False,
    ) -> None:
        self.calls = []
        self._responses = {url: dict(pages) for url, pages in (responses or {}).items()}
        self._repeating = repeating

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: Mapping[str, str],
    ) -> GmailHttpResponse:
        self.calls.append((method, url, dict(query)))
        if self._repeating:
            token = f"page-{len(self.calls)}"
            body = json.dumps({"threads": [], "nextPageToken": token}).encode()
            return GmailHttpResponse(status_code=200, body=body)
        page_token = query.get("pageToken")
        status_code, body = self._responses[url][page_token]
        return GmailHttpResponse(status_code=status_code, body=body)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _adapter(transport: FakeGmailTransport) -> GmailAdapter:
    clock: Clock = FixedClock(NOW)
    return GmailAdapter(transport, clock)


def _threads(
    name: str,
    *,
    status: int = 200,
) -> FakeGmailTransport:
    return FakeGmailTransport({GMAIL_THREADS_URL: {None: (status, _fixture(name))}})


def test_empty_inbox_is_success() -> None:
    transport = _threads("threads_empty.json")

    result = _adapter(transport).list_threads()

    assert result.threads == ()
    assert result.page_count == 1
    assert result.skipped_count == 0
    assert result.fetched_at == NOW.isoformat()


def test_profile_is_parsed() -> None:
    transport = FakeGmailTransport(
        {GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))}}
    )

    profile = _adapter(transport).read_profile()

    assert profile.email_address == "user@example.com"
    assert profile.messages_total == 12
    assert profile.threads_total == 4
    assert profile.history_id == "12345"


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


def test_read_inbox_threads_decodes_html_only_deadline() -> None:
    # Given: the deadline exists only in an HTML MIME body.
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

    # Then: the snippet remains usable while completeness is explicit.
    snapshot = result.threads[0]
    assert snapshot.body_text == "Fallback preview"
    assert snapshot.is_complete is False
    assert snapshot.degradation_reasons == ("body_snippet_fallback",)
    assert snapshot.provider_history_cursor == "12345"
    assert result.is_complete is False
    assert result.degradation_reasons == ("body_snippet_fallback",)


def test_pagination_follows_next_page_token() -> None:
    transport = FakeGmailTransport(
        {
            GMAIL_THREADS_URL: {
                None: (200, _fixture("threads_page1.json")),
                "page-2": (200, _fixture("threads_page2.json")),
            }
        }
    )

    result = _adapter(transport).list_threads()

    assert result.threads == (
        GmailThread(id="thread-a", history_id="1001"),
        GmailThread(id="thread-b", history_id="1002"),
        GmailThread(id="thread-c", history_id="1003"),
    )
    assert result.page_count == 2
    assert len(transport.calls) == 2
    assert "pageToken" not in transport.calls[0][2]
    assert transport.calls[1][2]["pageToken"] == "page-2"


def test_malformed_json_raises_parse_error() -> None:
    transport = _threads("malformed.json")

    with pytest.raises(GmailParseError) as caught:
        _ = _adapter(transport).list_threads()

    assert caught.value.error_code == "unknown"
    assert "CANARY_SNIPPET_DO_NOT_KEEP" not in str(caught.value)


def test_http_401_raises_auth_error() -> None:
    transport = _threads("error_unauthorized.json", status=401)

    with pytest.raises(GmailAuthError) as caught:
        _ = _adapter(transport).list_threads()

    assert caught.value.http_status == 401
    assert caught.value.error_code == "http_4xx"
    assert "CANARY_TOKEN_DO_NOT_LOG" not in str(caught.value)


def test_http_500_raises_normalized_server_error() -> None:
    transport = _threads("error_unauthorized.json", status=500)

    with pytest.raises(GmailParseError) as caught:
        _ = _adapter(transport).list_threads()

    assert caught.value.error_code == "http_5xx"


def test_requests_are_get_only_profile_and_threads() -> None:
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_empty.json"))},
        }
    )
    adapter = _adapter(transport)

    _ = adapter.read_profile()
    _ = adapter.list_threads()

    profile_method, profile_url, profile_query = transport.calls[0]
    thread_method, thread_url, thread_query = transport.calls[1]
    profile_parsed = urlparse(profile_url)
    thread_parsed = urlparse(thread_url)
    assert profile_method == "GET"
    assert thread_method == "GET"
    assert profile_url == GMAIL_PROFILE_URL
    assert thread_url == GMAIL_THREADS_URL
    assert profile_parsed.scheme == "https"
    assert thread_parsed.scheme == "https"
    assert profile_parsed.netloc == "gmail.googleapis.com"
    assert thread_parsed.netloc == "gmail.googleapis.com"
    assert profile_parsed.path == "/gmail/v1/users/me/profile"
    assert thread_parsed.path == "/gmail/v1/users/me/threads"
    assert profile_query == {}
    assert thread_query["maxResults"] == "100"
    assert thread_query["labelIds"] == "INBOX"
    assert "threads(id,historyId)" in thread_query["fields"]
    assert "snippet" not in thread_query["fields"]
    assert "format" not in thread_query


def test_skips_thread_missing_id() -> None:
    transport = _threads("skip_missing_id.json")

    result = _adapter(transport).list_threads()

    assert result.threads == (GmailThread(id="thread-good", history_id="2"),)
    assert result.skipped_count == 1


def test_page_cap_raises_parse_error() -> None:
    transport = FakeGmailTransport(repeating=True)

    with pytest.raises(GmailParseError):
        _ = _adapter(transport).list_threads()

    assert len(transport.calls) == DEFAULT_MAX_PAGES


def test_logs_and_errors_omit_email_pii(caplog: pytest.LogCaptureFixture) -> None:
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_page2.json"))},
        }
    )
    adapter = _adapter(transport)

    with caplog.at_level(logging.INFO, logger="proactive_mcp.sources.gmail"):
        profile = adapter.read_profile()
        result = adapter.list_threads()

    logged = " ".join(record.getMessage() for record in caplog.records)
    extras = " ".join(str(record.__dict__) for record in caplog.records)
    assert profile.email_address == "user@example.com"
    assert result.threads[0].id == "thread-c"
    assert "user@example.com" not in logged
    assert "CANARY_SNIPPET_DO_NOT_KEEP" not in logged
    assert "user@example.com" not in extras
    assert "CANARY_SNIPPET_DO_NOT_KEEP" not in extras
