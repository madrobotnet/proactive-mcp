"""Read-only Gmail profile and inbox-thread adapter."""

# noqa: SIZE_OK - Stable APIs and their provider wire models share one boundary.

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, TypeAlias, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from proactive_mcp.situations.inputs import (
    InboxThreadDegradationReason,
    InboxThreadSnapshot,
)

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock

GMAIL_PROFILE_URL: Final[str] = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GMAIL_THREADS_URL: Final[str] = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
DEFAULT_MAX_PAGES: Final[int] = 20
DEFAULT_MAX_RESULTS: Final[int] = 100
THREAD_FIELDS: Final[str] = "nextPageToken,threads(id,historyId)"
_HTTP_OK: Final[int] = 200
_HTTP_SERVER_ERROR_MIN: Final[int] = 500
_HTTP_SERVER_ERROR_MAX: Final[int] = 600
_MILLISECONDS_PER_SECOND: Final[int] = 1000

GmailErrorCode: TypeAlias = Literal["http_4xx", "http_5xx", "unknown"]
GmailDegradationReason: TypeAlias = Literal[
    "body_snippet_fallback",
    "thread_list_entry_skipped",
    "thread_without_projectable_message",
]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class GmailError(Exception):
    """A Gmail read failure with a machine-readable error code."""

    error_code: GmailErrorCode
    http_status: int | None = None

    def __post_init__(self) -> None:
        """Initialize the base exception with the error code only."""
        Exception.__init__(self, self.error_code)


@dataclass(frozen=True, slots=True)
class GmailAuthError(GmailError):
    """Raised when Google rejects Gmail credentials or scopes."""


@dataclass(frozen=True, slots=True)
class GmailParseError(GmailError):
    """Raised when a Gmail response cannot be parsed."""


@dataclass(frozen=True, slots=True)
class GmailHttpResponse:
    """A GET response body returned by an injected Gmail transport."""

    status_code: int
    body: bytes


class _GmailTransport(Protocol):
    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> GmailHttpResponse: ...


@dataclass(frozen=True, slots=True)
class GmailProfile:
    """A typed Gmail profile used to prove read access."""

    email_address: str
    messages_total: int
    threads_total: int
    history_id: str


@dataclass(frozen=True, slots=True)
class GmailThread:
    """A typed inbox thread with list metadata only."""

    id: str
    history_id: str | None


@dataclass(frozen=True, slots=True)
class GmailReadResult:
    """In-memory result of an inbox thread list."""

    threads: tuple[GmailThread, ...]
    fetched_at: str
    page_count: int
    skipped_count: int


@dataclass(frozen=True, slots=True)
class GmailInboxReadResult:
    """Detector-ready inbox projection with provider completeness metadata."""

    threads: tuple[InboxThreadSnapshot, ...]
    fetched_at: str
    provider_history_cursor: str
    page_count: int
    is_complete: bool
    degradation_reasons: tuple[GmailDegradationReason, ...]


class _Wire(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _WireProfile(_Wire):
    email_address: str = Field(alias="emailAddress")
    messages_total: int = Field(alias="messagesTotal")
    threads_total: int = Field(alias="threadsTotal")
    history_id: str = Field(alias="historyId")


class _WireThread(_Wire):
    id: str | None = None
    history_id: str | None = Field(default=None, alias="historyId")


class _WireThreadsPage(_Wire):
    threads: tuple[_WireThread, ...] = ()
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


class _WireHeader(_Wire):
    name: str
    value: str


class _WireBody(_Wire):
    data: str | None = None


class _WirePart(_Wire):
    mime_type: str | None = Field(default=None, alias="mimeType")
    headers: tuple[_WireHeader, ...] = ()
    body: _WireBody | None = None
    parts: tuple[_WirePart, ...] = ()


class _WireMessage(_Wire):
    id: str | None = None
    internal_date: int | None = Field(default=None, alias="internalDate")
    snippet: str | None = None
    payload: _WirePart | None = None


class _WireThreadDetail(_Wire):
    messages: tuple[_WireMessage, ...] = ()


_PROFILE_ADAPTER: Final[TypeAdapter[_WireProfile]] = TypeAdapter(_WireProfile)
_PAGE_ADAPTER: Final[TypeAdapter[_WireThreadsPage]] = TypeAdapter(_WireThreadsPage)
_THREAD_ADAPTER: Final[TypeAdapter[_WireThreadDetail]] = TypeAdapter(_WireThreadDetail)


class GmailAdapter:
    """Read Gmail profile and inbox threads through an injected GET-only transport."""

    _transport: _GmailTransport
    _clock: Clock

    def __init__(self, transport: _GmailTransport, clock: Clock) -> None:
        """Bind a GET-only transport and a UTC clock."""
        self._transport = transport
        self._clock = clock

    def read_profile(self) -> GmailProfile:
        """Return the authenticated user's typed Gmail profile."""
        body = _get(self._transport, GMAIL_PROFILE_URL, {})
        wire = _parse_json(_PROFILE_ADAPTER, body)
        return GmailProfile(
            email_address=wire.email_address,
            messages_total=wire.messages_total,
            threads_total=wire.threads_total,
            history_id=wire.history_id,
        )

    def list_threads(self) -> GmailReadResult:
        """Return typed inbox threads, discarding list snippets."""
        now = self._clock.now()
        threads: list[GmailThread] = []
        skipped_count = 0
        page_count = 0
        page_token: str | None = None
        while page_count < DEFAULT_MAX_PAGES:
            query = {
                "maxResults": str(DEFAULT_MAX_RESULTS),
                "labelIds": "INBOX",
                "fields": THREAD_FIELDS,
            }
            if page_token is not None:
                query["pageToken"] = page_token
            page = _parse_json(
                _PAGE_ADAPTER, _get(self._transport, GMAIL_THREADS_URL, query)
            )
            page_count += 1
            for item in page.threads:
                thread = _parse_thread(item)
                if thread is None:
                    skipped_count += 1
                else:
                    threads.append(thread)
            page_token = page.next_page_token
            if page_token is None:
                return GmailReadResult(
                    threads=tuple(threads),
                    fetched_at=now.isoformat(),
                    page_count=page_count,
                    skipped_count=skipped_count,
                )
        raise GmailParseError(error_code="unknown")

    def read_inbox_threads(self) -> GmailInboxReadResult:
        """Return detector-ready snapshots from profile, list, and thread reads."""
        profile = self.read_profile()
        listed = self.list_threads()
        snapshots: list[InboxThreadSnapshot] = []
        reasons: list[GmailDegradationReason] = []
        if listed.skipped_count:
            reasons.append("thread_list_entry_skipped")
        for thread in listed.threads:
            detail = _parse_json(
                _THREAD_ADAPTER,
                _get(
                    self._transport,
                    f"{GMAIL_THREADS_URL}/{quote(thread.id, safe='')}",
                    {"format": "full"},
                ),
            )
            snapshot = _project_thread(thread.id, detail, profile)
            if snapshot is None:
                reasons.append("thread_without_projectable_message")
            else:
                snapshots.append(snapshot)
                reasons.extend(snapshot.degradation_reasons)
        unique_reasons = tuple(dict.fromkeys(reasons))
        return GmailInboxReadResult(
            threads=tuple(snapshots),
            fetched_at=listed.fetched_at,
            provider_history_cursor=profile.history_id,
            page_count=listed.page_count,
            is_complete=not unique_reasons,
            degradation_reasons=unique_reasons,
        )


def _get(
    transport: _GmailTransport,
    url: str,
    query: dict[str, str],
) -> bytes:
    response = transport.request("GET", url, query)
    if response.status_code == _HTTP_OK:
        return response.body
    if response.status_code in {401, 403}:
        raise GmailAuthError(error_code="http_4xx", http_status=response.status_code)
    if _HTTP_SERVER_ERROR_MIN <= response.status_code < _HTTP_SERVER_ERROR_MAX:
        raise GmailParseError(error_code="http_5xx", http_status=response.status_code)
    raise GmailParseError(error_code="unknown", http_status=response.status_code)


def _parse_json(adapter: TypeAdapter[_T], body: bytes) -> _T:
    try:
        return adapter.validate_json(body)
    except ValidationError:
        raise GmailParseError(error_code="unknown") from None


def _parse_thread(wire: _WireThread) -> GmailThread | None:
    thread_id = wire.id
    if thread_id is None or thread_id == "":
        return None
    return GmailThread(id=thread_id, history_id=wire.history_id)


def _project_thread(
    thread_id: str,
    wire: _WireThreadDetail,
    profile: GmailProfile,
) -> InboxThreadSnapshot | None:
    candidates: list[tuple[datetime, str, _WireMessage]] = []
    for message in wire.messages:
        if message.id is None or message.internal_date is None:
            continue
        try:
            sent_at = datetime.fromtimestamp(
                message.internal_date / _MILLISECONDS_PER_SECOND,
                tz=UTC,
            )
        except (OSError, OverflowError, ValueError):
            continue
        candidates.append((sent_at, message.id, message))
    if not candidates:
        return None
    sent_at, message_id, latest = max(candidates, key=lambda item: (item[0], item[1]))
    headers = _headers(latest.payload)
    sender_name, sender_address = parseaddr(headers.get("from", ""))
    recipients = getaddresses([headers.get("to", "")])
    user_address = profile.email_address.casefold()
    body_text = _plain_text(latest.payload)
    degradation_reasons: tuple[InboxThreadDegradationReason, ...] = ()
    if body_text is None:
        body_text = latest.snippet
        degradation_reasons = ("body_snippet_fallback",)
    return InboxThreadSnapshot(
        thread_id=thread_id,
        latest_message_id=message_id,
        latest_from_user=sender_address.casefold() == user_address,
        user_is_recipient=any(
            address.casefold() == user_address for _, address in recipients
        ),
        latest_message_at=sent_at,
        subject=headers.get("subject") or None,
        sender_display=sender_name or sender_address or None,
        snippet=latest.snippet,
        body_text=body_text,
        is_complete=not degradation_reasons,
        degradation_reasons=degradation_reasons,
        provider_history_cursor=profile.history_id,
    )


def _headers(payload: _WirePart | None) -> dict[str, str]:
    if payload is None:
        return {}
    return {header.name.casefold(): header.value for header in payload.headers}


def _plain_text(part: _WirePart | None) -> str | None:
    if part is None:
        return None
    if part.mime_type is not None and part.mime_type.casefold() == "text/plain":
        if part.body is None or part.body.data is None:
            return None
        encoded = part.body.data
        padded = encoded + "=" * (-len(encoded) % 4)
        try:
            return base64.b64decode(
                padded,
                altchars=b"-_",
                validate=True,
            ).decode()
        except (binascii.Error, UnicodeDecodeError):
            return None
    for child in part.parts:
        body_text = _plain_text(child)
        if body_text is not None:
            return body_text
    return None
