from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import pytest

from proactive_mcp.sources.calendar import (
    CALENDAR_EVENTS_URL,
    DEFAULT_MAX_PAGES,
    AllDayDate,
    CalendarAdapter,
    CalendarAuthError,
    CalendarHttpResponse,
    CalendarParseError,
    TimedInstant,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from proactive_mcp.clock import Clock

FIXTURES = Path(__file__).parent / "fixtures" / "google" / "calendar"
NOW = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
WINDOW_START = "2026-07-11T09:00:00Z"
WINDOW_END = "2026-07-25T09:00:00Z"


class FixedClock:
    _now: datetime

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeCalendarTransport:
    """Map page tokens to fixture bytes without opening a socket."""

    calls: list[tuple[str, str, dict[str, str]]]
    _pages: dict[str | None, tuple[int, bytes]]
    _repeating: bool

    def __init__(
        self,
        pages: Mapping[str | None, tuple[int, bytes]] | None = None,
        *,
        repeating: bool = False,
    ) -> None:
        self.calls = []
        self._pages = dict(pages or {})
        self._repeating = repeating

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: Mapping[str, str],
    ) -> CalendarHttpResponse:
        self.calls.append((method, url, dict(query)))
        if self._repeating:
            token = f"page-{len(self.calls)}"
            body = json.dumps({"items": [], "nextPageToken": token}).encode()
            return CalendarHttpResponse(status_code=200, body=body)
        page_token = query.get("pageToken")
        status_code, body = self._pages[page_token]
        return CalendarHttpResponse(status_code=status_code, body=body)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _adapter(transport: FakeCalendarTransport) -> CalendarAdapter:
    clock: Clock = FixedClock(NOW)
    return CalendarAdapter(transport, clock)


def test_empty_calendar_is_success() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("empty.json"))})

    result = _adapter(transport).list_events()

    assert result.events == ()
    assert result.page_count == 1
    assert result.skipped_count == 0
    assert result.window_start == WINDOW_START
    assert result.window_end == WINDOW_END
    assert result.fetched_at == NOW.isoformat()


def test_timed_accepted_event_is_parsed() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("timed_accepted.json"))})

    result = _adapter(transport).list_events()

    assert len(result.events) == 1
    event = result.events[0]
    assert event.id == "evt-standup"
    assert event.status == "confirmed"
    assert event.summary == "Standup"
    assert isinstance(event.start, TimedInstant)
    assert event.start.instant == datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    assert event.start.is_all_day is False
    assert isinstance(event.end, TimedInstant)
    assert event.end.instant == datetime(2026, 7, 11, 10, 30, tzinfo=UTC)
    assert event.is_organizer is True
    assert event.self_response_status == "accepted"


def test_all_day_event_is_parsed() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("all_day.json"))})

    result = _adapter(transport).list_events()

    event = result.events[0]
    assert isinstance(event.start, AllDayDate)
    assert event.start.all_day_date == date(2026, 7, 18)
    assert event.start.is_all_day is True
    assert isinstance(event.end, AllDayDate)
    assert event.end.all_day_date == date(2026, 7, 19)
    assert event.end.is_all_day is True


def test_cancelled_event_without_start_is_kept() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("cancelled.json"))})

    result = _adapter(transport).list_events()

    event = result.events[0]
    assert event.id == "evt-cancelled"
    assert event.status == "cancelled"
    assert event.start is None
    assert event.end is None


def test_pagination_follows_next_page_token() -> None:
    transport = FakeCalendarTransport(
        {
            None: (200, _fixture("page1.json")),
            "page-2": (200, _fixture("page2.json")),
        }
    )

    result = _adapter(transport).list_events()

    assert [event.id for event in result.events] == ["evt-a", "evt-b", "evt-c"]
    assert result.page_count == 2
    assert len(transport.calls) == 2
    assert "pageToken" not in transport.calls[0][2]
    assert transport.calls[1][2]["pageToken"] == "page-2"
    assert transport.calls[0][2]["timeMin"] == transport.calls[1][2]["timeMin"]
    assert transport.calls[0][2]["timeMax"] == transport.calls[1][2]["timeMax"]


def test_malformed_json_raises_parse_error() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("malformed.json"))})

    with pytest.raises(CalendarParseError) as caught:
        _ = _adapter(transport).list_events()

    assert caught.value.error_code == "unknown"
    assert "Standup" not in str(caught.value)


def test_http_401_raises_auth_error() -> None:
    body = _fixture("error_unauthorized.json")
    transport = FakeCalendarTransport({None: (401, body)})

    with pytest.raises(CalendarAuthError) as caught:
        _ = _adapter(transport).list_events()

    assert caught.value.http_status == 401
    assert caught.value.error_code == "http_4xx"
    assert "CANARY_TOKEN_DO_NOT_LOG" not in str(caught.value)


def test_http_500_raises_normalized_server_error() -> None:
    body = _fixture("error_unauthorized.json")
    transport = FakeCalendarTransport({None: (500, body)})

    with pytest.raises(CalendarParseError) as caught:
        _ = _adapter(transport).list_events()

    assert caught.value.error_code == "http_5xx"


def test_default_window_uses_fake_clock() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("empty.json"))})

    _ = _adapter(transport).list_events()

    query = transport.calls[0][2]
    assert query["timeMin"] == WINDOW_START
    assert query["timeMax"] == WINDOW_END


def test_requests_are_get_only_primary_events() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("empty.json"))})

    _ = _adapter(transport).list_events()

    method, url, query = transport.calls[0]
    parsed = urlparse(url)
    assert method == "GET"
    assert url == CALENDAR_EVENTS_URL
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.googleapis.com"
    assert parsed.path == "/calendar/v3/calendars/primary/events"
    assert query["singleEvents"] == "true"
    assert query["showDeleted"] == "true"
    assert query["orderBy"] == "startTime"
    assert "attendees(self,responseStatus)" in query["fields"]
    assert "description" not in query["fields"]
    assert "attendees/email" not in query["fields"]


def test_skips_item_missing_id() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("skip_missing_id.json"))})

    result = _adapter(transport).list_events()

    assert [event.id for event in result.events] == ["evt-good"]
    assert result.skipped_count == 1


def test_overlapping_timed_events_are_not_conflicts() -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("overlap.json"))})

    result = _adapter(transport).list_events()

    assert [event.id for event in result.events] == ["evt-first", "evt-second"]
    assert not hasattr(result, "conflicts")


def test_page_cap_raises_parse_error() -> None:
    transport = FakeCalendarTransport(repeating=True)

    with pytest.raises(CalendarParseError):
        _ = _adapter(transport).list_events()

    assert len(transport.calls) == DEFAULT_MAX_PAGES


def test_logs_and_errors_omit_event_pii(caplog: pytest.LogCaptureFixture) -> None:
    transport = FakeCalendarTransport({None: (200, _fixture("timed_accepted.json"))})

    with caplog.at_level(logging.INFO, logger="proactive_mcp.sources.calendar"):
        result = _adapter(transport).list_events()

    logged = " ".join(record.getMessage() for record in caplog.records)
    extras = " ".join(str(record.__dict__) for record in caplog.records)
    assert result.events[0].summary == "Standup"
    assert "Standup" not in logged
    assert "user@example.com" not in logged
    assert "Standup" not in extras
    assert "user@example.com" not in extras
