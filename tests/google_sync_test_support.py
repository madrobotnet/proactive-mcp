from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from proactive_mcp.situations.inputs import InboxThreadSnapshot
from proactive_mcp.sources.calendar import CalendarEvent, CalendarReadResult
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStorageError,
)
from proactive_mcp.sources.gmail import GmailError, GmailInboxReadResult

if TYPE_CHECKING:
    from proactive_mcp.sources.google_sync import (
        GoogleTransportError,
        InvalidGrantError,
    )

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FailingGmailReader:
    error: GmailError | GoogleTransportError | InvalidGrantError

    def read_inbox_threads(self) -> GmailInboxReadResult:
        raise self.error


@dataclass(frozen=True, slots=True)
class FakeInboxReader:
    result: GmailInboxReadResult

    def read_inbox_threads(self) -> GmailInboxReadResult:
        return self.result


@dataclass(frozen=True, slots=True)
class FakeCalendarReader:
    result: CalendarReadResult

    def list_events(self) -> CalendarReadResult:
        return self.result


@dataclass(frozen=True, slots=True)
class FakeCredentials:
    """Record credential deletion as the observable fake behavior."""

    delete_calls: list[bool] = field(default_factory=list)
    delete_error: CredentialStorageError | None = None

    @property
    def refresh_token(self) -> str:
        """Provide a non-secret durable token marker for the OAuth fake."""
        return "redacted"

    @property
    def scopes(self) -> tuple[str, str]:
        """Provide the fixed read-only scopes required by the credential contract."""
        return GOOGLE_READONLY_SCOPES

    def to_json(self) -> str:
        """Provide an inert serialization because this fake is never persisted."""
        return "{}"

    def delete(self) -> None:
        self.delete_calls.append(True)
        if self.delete_error is not None:
            raise self.delete_error


def calendar_result() -> CalendarReadResult:
    return CalendarReadResult(
        events=(
            CalendarEvent(
                id="event-1",
                status="confirmed",
                summary="Private planning event",
                start=None,
                end=None,
                is_organizer=True,
                self_response_status="accepted",
            ),
        ),
        fetched_at=NOW.isoformat(),
        window_start=NOW.isoformat(),
        window_end=NOW.isoformat(),
        page_count=1,
        skipped_count=0,
    )


def gmail_inbox_result() -> GmailInboxReadResult:
    thread = InboxThreadSnapshot(
        thread_id="thread-1",
        latest_message_id="message-1",
        latest_from_user=False,
        user_is_recipient=True,
        latest_message_at=NOW,
    )
    return GmailInboxReadResult(
        threads=(thread,),
        fetched_at=NOW.isoformat(),
        provider_history_cursor="history-2",
        page_count=1,
        coverage_complete=True,
        degradation_reasons=(),
    )
