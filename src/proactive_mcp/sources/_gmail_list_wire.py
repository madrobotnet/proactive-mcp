"""Parse Gmail profile and thread-list JSON into typed values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ._gmail_models import GmailParseError, GmailProfile, GmailThread

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


@dataclass(frozen=True, slots=True)
class ThreadListPage:
    """One parsed Gmail threads.list page."""

    threads: tuple[GmailThread | None, ...]
    next_page_token: str | None


def parse_json(adapter: TypeAdapter[_T], body: bytes) -> _T:
    """Parse JSON bytes with a TypeAdapter, mapping failures to GmailParseError."""
    try:
        return adapter.validate_json(body)
    except ValidationError:
        raise GmailParseError(error_code="unknown") from None


def parse_profile(body: bytes) -> GmailProfile:
    """Parse a Gmail users.getProfile body."""
    wire = parse_json(_PROFILE_ADAPTER, body)
    return GmailProfile(
        email_address=wire.email_address,
        messages_total=wire.messages_total,
        threads_total=wire.threads_total,
        history_id=wire.history_id,
    )


def parse_thread_list_page(body: bytes) -> ThreadListPage:
    """Parse a Gmail users.threads.list page, keeping invalid rows as None."""
    page = parse_json(_PAGE_ADAPTER, body)
    return ThreadListPage(
        threads=tuple(_parse_list_thread(item) for item in page.threads),
        next_page_token=page.next_page_token,
    )


def _parse_list_thread(wire: _WireThread) -> GmailThread | None:
    thread_id = wire.id
    if thread_id is None or thread_id == "":
        return None
    return GmailThread(id=thread_id, history_id=wire.history_id)


__all__ = [
    "ThreadListPage",
    "parse_json",
    "parse_profile",
    "parse_thread_list_page",
]
