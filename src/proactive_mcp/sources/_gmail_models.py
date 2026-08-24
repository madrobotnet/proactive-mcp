"""Typed Gmail transport and read-result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from ._gmail_projection import ProjectionDegradationReason

if TYPE_CHECKING:
    from proactive_mcp.situations.inputs import InboxThreadSnapshot

GmailErrorCode: TypeAlias = Literal["http_4xx", "http_5xx", "resource_limit", "unknown"]
GmailDegradationReason: TypeAlias = (
    Literal[
        "pagination_limit",
        "sync_budget_exhausted",
        "thread_projection_limit",
        "thread_response_too_large",
        "thread_list_entry_skipped",
        "thread_without_projectable_message",
    ]
    | ProjectionDegradationReason
)


@dataclass(frozen=True, slots=True)
class GmailError(Exception):
    """A Gmail read failure with a machine-readable error code."""

    error_code: GmailErrorCode
    http_status: int | None = None

    def __post_init__(self) -> None:
        """Initialize the base exception with the error code only."""
        Exception.__init__(self, self.error_code)


@dataclass(frozen=True, slots=True)
class GmailBodyLimitError(GmailError):
    """Signal a production transport byte or pass-budget boundary."""

    limit_reason: Literal["response", "sync"] = "response"


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
    limit_reason: Literal["response", "sync"] | None = None


class GmailTransport(Protocol):
    """Issue read-only Gmail requests through an injected transport."""

    def request(
        self,
        method: Literal["GET"],
        url: str,
        query: dict[str, str],
    ) -> GmailHttpResponse:
        """Return one bounded Gmail HTTP response."""
        ...


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
    is_complete: bool
    degradation_reasons: tuple[GmailDegradationReason, ...]


@dataclass(frozen=True, slots=True)
class GmailInboxReadResult:
    """Detector-ready inbox projection with provider completeness metadata."""

    threads: tuple[InboxThreadSnapshot, ...]
    fetched_at: str
    provider_history_cursor: str
    page_count: int
    is_complete: bool
    degradation_reasons: tuple[GmailDegradationReason, ...]
    allows_absent_resolution: bool = False
    resolution_safe_thread_ids: frozenset[str] = frozenset()
    resolution_excluded_thread_ids: frozenset[str] = frozenset()


__all__ = [
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
