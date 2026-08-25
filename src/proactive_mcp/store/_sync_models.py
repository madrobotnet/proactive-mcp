"""Closed public models for Google source synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import (
    datetime,  # noqa: TC003 - Pydantic resolves this annotation at runtime.
)
from typing import Final, Literal, TypeAlias

SourceName = Literal["gmail", "calendar"]
SourceAuthState = Literal["not_configured", "configured", "needs_reauth"]
SourceErrorCode = Literal[
    "invalid_grant",
    "scope_mismatch",
    "http_4xx",
    "http_5xx",
    "network",
    "resource_limit",
    "timeout",
    "degraded",
    "unknown",
]
SourceSyncFailureCode = Literal[
    "scope_mismatch",
    "http_4xx",
    "http_5xx",
    "network",
    "resource_limit",
    "timeout",
    "degraded",
    "unknown",
]
SourceReadOutcome: TypeAlias = Literal[
    "healthy",
    "partial",
    "stale",
    "auth_error",
    "transport_error",
]
SourceReadReason: TypeAlias = Literal[
    "body_snippet_fallback",
    "body_truncated",
    "degraded",
    "direction_metadata_ambiguous",
    "direction_metadata_missing",
    "http_4xx",
    "http_5xx",
    "identity_headers_ambiguous",
    "invalid_grant",
    "mime_structure_truncated",
    "network",
    "never_synced",
    "not_configured",
    "pagination_limit",
    "resource_limit",
    "scope_mismatch",
    "stale",
    "sync_budget_exhausted",
    "thread_list_entry_skipped",
    "thread_projection_limit",
    "thread_response_too_large",
    "thread_without_projectable_message",
    "timeout",
    "unknown",
]
GMAIL_READ_BYTE_BUDGET: Final[int] = 8_000_000
_SOURCE_ERROR_OUTCOMES: Final[dict[SourceErrorCode, SourceReadOutcome]] = {
    "invalid_grant": "auth_error",
    "scope_mismatch": "auth_error",
    "http_4xx": "auth_error",
    "resource_limit": "partial",
    "degraded": "partial",
    "http_5xx": "transport_error",
    "network": "transport_error",
    "timeout": "transport_error",
    "unknown": "transport_error",
}


@dataclass(frozen=True, slots=True)
class SourceReadReasonCount:
    """One closed diagnostic reason and its occurrence count."""

    reason: SourceReadReason
    count: int


@dataclass(frozen=True, slots=True)
class SourceReadDiagnostics:
    """PII-free counters and outcome for one Gmail read projection."""

    outcome: SourceReadOutcome
    request_count: int
    page_count: int
    projected_count: int
    excluded_count: int
    byte_budget: int
    reason_counts: tuple[SourceReadReasonCount, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceSyncState:
    """The persisted synchronization state for one Google source."""

    source: SourceName
    auth_state: SourceAuthState
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: SourceErrorCode | None
    sync_cursor: str | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class SourceHealthSnapshot:
    """Google source states and accepted Gmail diagnostics from one read."""

    gmail: SourceSyncState
    calendar: SourceSyncState
    gmail_diagnostics: SourceReadDiagnostics | None


class InvalidSourceReadDiagnosticsError(ValueError):
    """Raised when diagnostics contain values outside the closed store contract."""

    def __init__(self) -> None:
        """Initialize a PII-free boundary error."""
        super().__init__("invalid Gmail diagnostics")


def source_failure_diagnostics(
    error_code: SourceErrorCode,
) -> SourceReadDiagnostics:
    """Map one normalized failure to its closed source outcome."""
    return SourceReadDiagnostics(
        outcome=_SOURCE_ERROR_OUTCOMES[error_code],
        request_count=0,
        page_count=0,
        projected_count=0,
        excluded_count=0,
        byte_budget=GMAIL_READ_BYTE_BUDGET,
        reason_counts=(SourceReadReasonCount(error_code, 1),),
    )


def unconfigured_state(source: SourceName) -> SourceSyncState:
    """Return the explicit never-configured state for one source."""
    return SourceSyncState(
        source=source,
        auth_state="not_configured",
        last_success_at=None,
        last_attempt_at=None,
        last_error_code=None,
        sync_cursor=None,
        updated_at=None,
    )
