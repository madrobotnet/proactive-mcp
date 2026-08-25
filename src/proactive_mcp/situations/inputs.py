"""Typed source snapshots consumed by the situation detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar

if TYPE_CHECKING:
    from datetime import datetime

    from proactive_mcp.sources.calendar import CalendarEvent
    from proactive_mcp.store import SourceErrorCode, SourceGeneration

InboxThreadDegradationReason: TypeAlias = Literal[
    "body_snippet_fallback",
    "body_truncated",
    "direction_metadata_ambiguous",
    "direction_metadata_missing",
    "identity_headers_ambiguous",
    "mime_structure_truncated",
]
SnapshotItem = TypeVar("SnapshotItem")

__all__ = [
    "EngineInputs",
    "InboxThreadDegradationReason",
    "InboxThreadSnapshot",
    "SourceSnapshot",
]


@dataclass(frozen=True, slots=True)
class InboxThreadSnapshot:
    """One inbox thread reduced to the fields the reply detector needs.

    ``subject``, ``sender_display``, and ``snippet`` quote external mail
    content; detectors must only surface them inside evidence.
    """

    thread_id: str
    latest_message_id: str
    latest_from_user: bool
    user_is_recipient: bool
    latest_message_at: datetime
    subject: str | None = None
    sender_display: str | None = None
    snippet: str | None = None
    body_text: str | None = None
    resolution_safe: bool = True
    degradation_reasons: tuple[InboxThreadDegradationReason, ...] = ()
    provider_history_cursor: str | None = None

    @property
    def is_complete(self) -> bool:
        """Return the compatibility alias for resolution safety."""
        return self.resolution_safe


@dataclass(frozen=True, slots=True)
class SourceSnapshot(Generic[SnapshotItem]):
    """One ordered source result ready for atomic Situation application."""

    generation: SourceGeneration
    items: tuple[SnapshotItem, ...]
    complete: bool = True
    sync_cursor: str | None = None
    warning_codes: tuple[str, ...] = ()
    error_code: SourceErrorCode | None = None
    resolve_absent: bool = False
    resolution_scope_ids: frozenset[str] = frozenset()
    resolution_excluded_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EngineInputs:
    """Ordered source snapshots for one evaluation pass.

    ``None`` means the source was skipped. A degraded snapshot may add
    positive detections but must not resolve absent stored situations.
    """

    gmail_threads: SourceSnapshot[InboxThreadSnapshot] | None = None
    calendar_events: SourceSnapshot[CalendarEvent] | None = None
