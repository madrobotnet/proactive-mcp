"""Read-only Gmail profile and inbox-thread adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ._gmail_models import (
    GmailAuthError,
    GmailBodyLimitError,
    GmailDegradationReason,
    GmailError,
    GmailErrorCode,
    GmailHttpResponse,
    GmailInboxReadResult,
    GmailParseError,
    GmailProfile,
    GmailReadResult,
    GmailThread,
    GmailTransport,
)
from ._gmail_projection import (
    THREAD_DETAIL_ADAPTER,
    project_thread,
)

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock
    from proactive_mcp.situations.inputs import InboxThreadSnapshot

GMAIL_PROFILE_URL: Final[str] = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GMAIL_THREADS_URL: Final[str] = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
DEFAULT_MAX_PAGES: Final[int] = 20
DEFAULT_MAX_RESULTS: Final[int] = 100
DEFAULT_MAX_PROJECTED_THREADS: Final[int] = 200
THREAD_FIELDS: Final[str] = "nextPageToken,threads(id,historyId)"
_HTTP_OK: Final[int] = 200
_HTTP_SERVER_ERROR_MIN: Final[int] = 500
_HTTP_SERVER_ERROR_MAX: Final[int] = 600
_MAX_THREAD_RESPONSE_BYTES: Final[int] = 1_000_000

_T = TypeVar("_T")


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


_PROFILE_ADAPTER: Final[TypeAdapter[_WireProfile]] = TypeAdapter(_WireProfile)
_PAGE_ADAPTER: Final[TypeAdapter[_WireThreadsPage]] = TypeAdapter(_WireThreadsPage)


class GmailAdapter:
    """Read Gmail profile and inbox threads through an injected GET-only transport."""

    _transport: GmailTransport
    _clock: Clock
    _max_projected_threads: int

    def __init__(
        self,
        transport: GmailTransport,
        clock: Clock,
        *,
        max_projected_threads: int = DEFAULT_MAX_PROJECTED_THREADS,
    ) -> None:
        """Bind a GET-only transport and a UTC clock."""
        self._transport = transport
        self._clock = clock
        self._max_projected_threads = max_projected_threads

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
        seen_tokens: set[str] = set()
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
                    is_complete=True,
                    degradation_reasons=(),
                )
            if page_token in seen_tokens:
                raise GmailParseError(error_code="unknown")
            seen_tokens.add(page_token)
        return GmailReadResult(
            threads=tuple(threads),
            fetched_at=now.isoformat(),
            page_count=page_count,
            skipped_count=skipped_count,
            is_complete=False,
            degradation_reasons=("pagination_limit",),
        )

    def read_inbox_threads(self) -> GmailInboxReadResult:
        """Return detector-ready snapshots from profile, list, and thread reads."""
        profile = self.read_profile()
        listed = self.list_threads()
        snapshots: list[InboxThreadSnapshot] = []
        reasons: list[GmailDegradationReason] = list(listed.degradation_reasons)
        source_is_complete = listed.is_complete
        excluded_thread_ids: set[str] = set()
        if listed.skipped_count:
            reasons.append("thread_list_entry_skipped")
            source_is_complete = False
        projected_threads = listed.threads[: self._max_projected_threads]
        if len(projected_threads) < len(listed.threads):
            reasons.append("thread_projection_limit")
            source_is_complete = False
            excluded_thread_ids.update(
                thread.id for thread in listed.threads[len(projected_threads) :]
            )
        for thread in projected_threads:
            try:
                response_body = _get(
                    self._transport,
                    f"{GMAIL_THREADS_URL}/{quote(thread.id, safe='')}",
                    {"format": "full"},
                )
            except GmailBodyLimitError as error:
                source_is_complete = False
                if error.limit_reason == "sync":
                    reasons.append("sync_budget_exhausted")
                    start = projected_threads.index(thread)
                    excluded_thread_ids.update(
                        item.id for item in projected_threads[start:]
                    )
                    break
                reasons.append("thread_response_too_large")
                excluded_thread_ids.add(thread.id)
                continue
            if len(response_body) > _MAX_THREAD_RESPONSE_BYTES:
                reasons.append("thread_response_too_large")
                source_is_complete = False
                excluded_thread_ids.add(thread.id)
                continue
            detail = _parse_json(
                THREAD_DETAIL_ADAPTER,
                response_body,
            )
            snapshot = project_thread(
                thread.id,
                detail,
                profile_email=profile.email_address,
                profile_history_cursor=profile.history_id,
            )
            if snapshot is None:
                reasons.append("thread_without_projectable_message")
                source_is_complete = False
                excluded_thread_ids.add(thread.id)
            else:
                snapshots.append(snapshot)
                reasons.extend(snapshot.degradation_reasons)
                if not snapshot.is_complete:
                    excluded_thread_ids.add(thread.id)
        unique_reasons = tuple(dict.fromkeys(reasons))
        return GmailInboxReadResult(
            threads=tuple(snapshots),
            fetched_at=listed.fetched_at,
            provider_history_cursor=profile.history_id,
            page_count=listed.page_count,
            is_complete=source_is_complete,
            degradation_reasons=unique_reasons,
            allows_absent_resolution=(
                listed.is_complete
                and listed.skipped_count == 0
                and len(projected_threads) == len(listed.threads)
            ),
            resolution_safe_thread_ids=frozenset(
                item.thread_id for item in snapshots if item.is_complete
            ),
            resolution_excluded_thread_ids=frozenset(excluded_thread_ids),
        )


def _get(
    transport: GmailTransport,
    url: str,
    query: dict[str, str],
) -> bytes:
    response = transport.request("GET", url, query)
    if response.limit_reason is not None:
        raise GmailBodyLimitError(
            error_code="resource_limit",
            limit_reason=response.limit_reason,
        )
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


__all__ = [
    "DEFAULT_MAX_PAGES",
    "GMAIL_PROFILE_URL",
    "GMAIL_THREADS_URL",
    "GmailAdapter",
    "GmailAuthError",
    "GmailDegradationReason",
    "GmailError",
    "GmailErrorCode",
    "GmailHttpResponse",
    "GmailInboxReadResult",
    "GmailParseError",
    "GmailProfile",
    "GmailReadResult",
    "GmailThread",
    "GmailTransport",
]
