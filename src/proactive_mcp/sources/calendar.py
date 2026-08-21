"""Read-only Google Calendar events adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock

CALENDAR_EVENTS_URL: Final[str] = (
    "https://www.googleapis.com/calendar/v3/calendars/primary/events"
)
DEFAULT_LOOKAHEAD: Final[timedelta] = timedelta(days=14)
DEFAULT_MAX_PAGES: Final[int] = 20
DEFAULT_MAX_RESULTS: Final[int] = 250
EVENT_FIELDS: Final[str] = (
    "items(id,status,summary,start,end,transparency,eventType,"
    "recurringEventId,organizer/self,attendees(self,responseStatus)),nextPageToken"
)
_HTTP_OK: Final[int] = 200
_HTTP_SERVER_ERROR_MIN: Final[int] = 500
_HTTP_SERVER_ERROR_MAX: Final[int] = 600

EventStatus: TypeAlias = Literal["confirmed", "tentative", "cancelled"]
ResponseStatus: TypeAlias = Literal["needsAction", "declined", "tentative", "accepted"]
CalendarErrorCode: TypeAlias = Literal["http_4xx", "http_5xx", "unknown"]
_STATUSES: Final[dict[str, EventStatus]] = {
    "confirmed": "confirmed",
    "tentative": "tentative",
    "cancelled": "cancelled",
}
_RESPONSES: Final[dict[str, ResponseStatus]] = {
    "needsAction": "needsAction",
    "declined": "declined",
    "tentative": "tentative",
    "accepted": "accepted",
}


@dataclass(frozen=True, slots=True)
class CalendarError(Exception):
    """A Calendar read failure with a machine-readable error code."""

    error_code: CalendarErrorCode
    http_status: int | None = None

    def __post_init__(self) -> None:
        """Initialize the base exception with the error code only."""
        Exception.__init__(self, self.error_code)


@dataclass(frozen=True, slots=True)
class CalendarAuthError(CalendarError):
    """Raised when Google rejects Calendar credentials or scopes."""


@dataclass(frozen=True, slots=True)
class CalendarParseError(CalendarError):
    """Raised when a Calendar response cannot be parsed."""


@dataclass(frozen=True, slots=True)
class CalendarHttpResponse:
    """A GET response body returned by an injected Calendar transport."""

    status_code: int
    body: bytes


class _CalendarTransport(Protocol):
    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> CalendarHttpResponse: ...


@dataclass(frozen=True, slots=True)
class TimedInstant:
    """A timezone-aware event bound expressed as a UTC instant."""

    instant: datetime
    is_all_day: bool = False


@dataclass(frozen=True, slots=True)
class AllDayDate:
    """An all-day event bound expressed as a calendar date."""

    all_day_date: date
    is_all_day: bool = True


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """A typed Calendar event with the flags later detectors need."""

    id: str
    status: EventStatus
    summary: str | None
    start: TimedInstant | AllDayDate | None
    end: TimedInstant | AllDayDate | None
    is_organizer: bool
    self_response_status: ResponseStatus | None


@dataclass(frozen=True, slots=True)
class CalendarReadResult:
    """In-memory result of a primary-calendar events read."""

    events: tuple[CalendarEvent, ...]
    fetched_at: str
    window_start: str
    window_end: str
    page_count: int
    skipped_count: int


class _Wire(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _WireEventTime(_Wire):
    date: str | None = None
    date_time: str | None = Field(default=None, alias="dateTime")


class _WireAttendee(_Wire):
    is_self: bool = Field(default=False, alias="self")
    response_status: str | None = Field(default=None, alias="responseStatus")


class _WireOrganizer(_Wire):
    is_self: bool = Field(default=False, alias="self")


class _WireEvent(_Wire):
    id: str | None = None
    status: str | None = None
    summary: str | None = None
    start: _WireEventTime | None = None
    end: _WireEventTime | None = None
    organizer: _WireOrganizer | None = None
    attendees: tuple[_WireAttendee, ...] = ()


class _WireEventsPage(_Wire):
    items: tuple[_WireEvent, ...]
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


_PAGE_ADAPTER: Final[TypeAdapter[_WireEventsPage]] = TypeAdapter(_WireEventsPage)


class CalendarAdapter:
    """List primary-calendar events through an injected GET-only transport."""

    _transport: _CalendarTransport
    _clock: Clock

    def __init__(self, transport: _CalendarTransport, clock: Clock) -> None:
        """Bind a GET-only transport and a UTC clock."""
        self._transport = transport
        self._clock = clock

    def list_events(self) -> CalendarReadResult:
        """Return typed primary-calendar events for the default lookahead window."""
        now = self._clock.now()
        window_start = _rfc3339_z(now)
        window_end = _rfc3339_z(now + DEFAULT_LOOKAHEAD)
        events: list[CalendarEvent] = []
        skipped_count = 0
        page_count = 0
        page_token: str | None = None
        while page_count < DEFAULT_MAX_PAGES:
            query = {
                "singleEvents": "true",
                "showDeleted": "true",
                "orderBy": "startTime",
                "maxResults": str(DEFAULT_MAX_RESULTS),
                "timeMin": window_start,
                "timeMax": window_end,
                "fields": EVENT_FIELDS,
            }
            if page_token is not None:
                query["pageToken"] = page_token
            response = self._transport.request("GET", CALENDAR_EVENTS_URL, query)
            if response.status_code != _HTTP_OK:
                if response.status_code in {401, 403}:
                    raise CalendarAuthError(
                        error_code="http_4xx", http_status=response.status_code
                    )
                if (
                    _HTTP_SERVER_ERROR_MIN
                    <= response.status_code
                    < _HTTP_SERVER_ERROR_MAX
                ):
                    raise CalendarParseError(
                        error_code="http_5xx",
                        http_status=response.status_code,
                    )
                raise CalendarParseError(
                    error_code="unknown", http_status=response.status_code
                )
            try:
                page = _PAGE_ADAPTER.validate_json(response.body)
            except ValidationError:
                raise CalendarParseError(error_code="unknown") from None
            page_count += 1
            for item in page.items:
                event = _parse_event(item)
                if event is None:
                    skipped_count += 1
                else:
                    events.append(event)
            page_token = page.next_page_token
            if page_token is None:
                return CalendarReadResult(
                    events=tuple(events),
                    fetched_at=now.isoformat(),
                    window_start=window_start,
                    window_end=window_end,
                    page_count=page_count,
                    skipped_count=skipped_count,
                )
        raise CalendarParseError(error_code="unknown")


def _rfc3339_z(value: datetime) -> str:
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _parse_event(wire: _WireEvent) -> CalendarEvent | None:
    event_id = wire.id
    status = _parse_status(wire.status)
    if event_id is None or event_id == "" or status is None:
        return None
    bounds = _parse_bounds(status, wire.start, wire.end)
    if bounds is None:
        return None
    start, end = bounds
    organizer = wire.organizer
    self_status: ResponseStatus | None = None
    for attendee in wire.attendees:
        if attendee.is_self:
            self_status = _parse_response(attendee.response_status)
            break
    return CalendarEvent(
        id=event_id,
        status=status,
        summary=wire.summary,
        start=start,
        end=end,
        is_organizer=organizer.is_self if organizer is not None else False,
        self_response_status=self_status,
    )


def _parse_bounds(
    status: EventStatus,
    start_wire: _WireEventTime | None,
    end_wire: _WireEventTime | None,
) -> tuple[TimedInstant | AllDayDate | None, TimedInstant | AllDayDate | None] | None:
    if start_wire is None and end_wire is None:
        if status != "cancelled":
            return None
        return None, None
    if start_wire is None or end_wire is None:
        return None
    start = _parse_event_time(start_wire)
    end = _parse_event_time(end_wire)
    if start is None or end is None:
        return None
    return start, end


def _parse_event_time(wire: _WireEventTime) -> TimedInstant | AllDayDate | None:
    if wire.date_time is not None:
        raw = wire.date_time
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return TimedInstant(parsed.astimezone(UTC))
    if wire.date is None:
        return None
    try:
        return AllDayDate(date.fromisoformat(wire.date))
    except ValueError:
        return None


def _parse_status(raw: str | None) -> EventStatus | None:
    return _STATUSES.get("confirmed" if raw is None else raw)


def _parse_response(raw: str | None) -> ResponseStatus | None:
    if raw is None:
        return None
    return _RESPONSES.get(raw)
