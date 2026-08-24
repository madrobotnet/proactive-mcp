from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import pytest

from proactive_mcp.sources.gmail import (
    DEFAULT_MAX_PAGES,
    GMAIL_PROFILE_URL,
    GMAIL_THREADS_URL,
    GmailAdapter,
    GmailAuthError,
    GmailHttpResponse,
    GmailLookbackError,
    GmailParseError,
    GmailThread,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from proactive_mcp.clock import Clock

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "gmail"
NOW = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
SEVEN_DAY_LOOKBACK = timedelta(days=7)
SEVEN_DAY_Q = f"after:{int((NOW - SEVEN_DAY_LOOKBACK).timestamp())}"


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeGmailTransport:
    """Map URL plus page token to fixture bytes without opening a socket."""

    _responses: dict[str, dict[str | None, tuple[int, bytes]]]
    _repeating: bool

    def __init__(
        self,
        responses: Mapping[str, Mapping[str | None, tuple[int, bytes]]] | None = None,
        *,
        repeating: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
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
        status_code, body = self._responses[url][query.get("pageToken")]
        return GmailHttpResponse(status_code=status_code, body=body)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _adapter(transport: FakeGmailTransport) -> GmailAdapter:
    clock: Clock = FixedClock()
    return GmailAdapter(transport, clock)


def _threads(name: str, *, status: int = 200) -> FakeGmailTransport:
    return FakeGmailTransport({GMAIL_THREADS_URL: {None: (status, _fixture(name))}})


def test_empty_inbox_is_success() -> None:
    result = _adapter(_threads("threads_empty.json")).list_threads()

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
    first_query = transport.calls[0][2]
    second_query = transport.calls[1][2]
    assert "pageToken" not in first_query
    assert second_query["pageToken"] == "page-2"
    assert first_query["labelIds"] == "INBOX"
    assert second_query["labelIds"] == "INBOX"
    assert first_query["q"] == SEVEN_DAY_Q
    assert second_query["q"] == first_query["q"]


def test_malformed_json_raises_parse_error() -> None:
    with pytest.raises(GmailParseError) as caught:
        _ = _adapter(_threads("malformed.json")).list_threads()

    assert caught.value.error_code == "unknown"
    assert "CANARY_SNIPPET_DO_NOT_KEEP" not in str(caught.value)


def test_http_401_raises_auth_error() -> None:
    with pytest.raises(GmailAuthError) as caught:
        _ = _adapter(_threads("error_unauthorized.json", status=401)).list_threads()

    assert caught.value.http_status == 401
    assert caught.value.error_code == "http_4xx"
    assert "CANARY_TOKEN_DO_NOT_LOG" not in str(caught.value)


def test_http_500_raises_normalized_server_error() -> None:
    with pytest.raises(GmailParseError) as caught:
        _ = _adapter(_threads("error_unauthorized.json", status=500)).list_threads()

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
    assert thread_query["q"] == SEVEN_DAY_Q
    assert "newer_than" not in thread_query["q"]
    assert "threads(id,historyId)" in thread_query["fields"]
    assert "snippet" not in thread_query["fields"]
    assert "format" not in thread_query


def test_list_threads_sends_seven_day_after_epoch() -> None:
    transport = _threads("threads_empty.json")

    _ = _adapter(transport).list_threads()

    query = transport.calls[0][2]
    assert query["labelIds"] == "INBOX"
    assert query["q"] == SEVEN_DAY_Q
    assert query["q"] == "after:1783155600"
    assert "newer_than" not in query["q"]


@pytest.mark.parametrize("lookback", [timedelta(0), timedelta(days=-3)])
def test_non_positive_lookback_is_rejected_at_construction(
    lookback: timedelta,
) -> None:
    transport = FakeGmailTransport()

    with pytest.raises(GmailLookbackError) as caught:
        _ = GmailAdapter(transport, FixedClock(), lookback=lookback)

    assert caught.value.lookback == lookback
    assert transport.calls == []


def test_list_threads_sends_injected_lookback_after_epoch() -> None:
    transport = _threads("threads_empty.json")
    lookback = timedelta(days=3)
    clock: Clock = FixedClock()

    _ = GmailAdapter(transport, clock, lookback=lookback).list_threads()

    query = transport.calls[0][2]
    assert query["labelIds"] == "INBOX"
    assert query["q"] == f"after:{int((NOW - lookback).timestamp())}"
    assert query["q"] != SEVEN_DAY_Q
    assert "newer_than" not in query["q"]


def test_skips_thread_missing_id() -> None:
    result = _adapter(_threads("skip_missing_id.json")).list_threads()

    assert result.threads == (GmailThread(id="thread-good", history_id="2"),)
    assert result.skipped_count == 1


def test_page_cap_returns_bounded_partial_result() -> None:
    transport = FakeGmailTransport(repeating=True)

    result = _adapter(transport).list_threads()

    assert len(transport.calls) == DEFAULT_MAX_PAGES
    assert result.threads == ()
    assert result.is_complete is False
    assert result.degradation_reasons == ("pagination_limit",)


def test_inbox_result_exposes_bounded_request_and_thread_counts() -> None:
    # Given: one listed thread has one projectable detail response.
    thread_url = f"{GMAIL_THREADS_URL}/thread-deadline"
    transport = FakeGmailTransport(
        {
            GMAIL_PROFILE_URL: {None: (200, _fixture("profile.json"))},
            GMAIL_THREADS_URL: {None: (200, _fixture("threads_deadline.json"))},
            thread_url: {None: (200, _fixture("thread_deadline.json"))},
        }
    )

    # When: the adapter returns its in-memory projection result.
    result = _adapter(transport).read_inbox_threads()

    # Then: bounded work and coverage are exposed as counts.
    assert result.request_count == 3
    assert result.page_count == 1
    assert result.projected_thread_count == 1
    assert result.excluded_thread_count == 0


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
