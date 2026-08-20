"""M1 memory MCP request validation and tool implementations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from inspect import Parameter, Signature
from pathlib import Path
from typing import Annotated, ClassVar, Literal, TypedDict, Unpack

from pydantic import BaseModel, ConfigDict, Field

from proactive_mcp.store import (
    MemoryItem,
    MemoryKind,
    MemoryRecurrence,
    MemorySource,
    NewMemory,
    Store,
)


class MemoryItemResponse(BaseModel):
    """Serialized memory item returned by MCP memory tools."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    kind: MemoryKind
    entity: str | None
    content: str
    date_anchor: str | None
    recurrence: MemoryRecurrence
    lead_days: int | None
    source: MemorySource
    created_at: str
    updated_at: str
    archived: bool


class RecallResponse(BaseModel):
    """Serialized recall result containing matching memory items."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: tuple[MemoryItemResponse, ...]


class ForgetResponse(BaseModel):
    """Serialized result of archiving a memory item."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    archived: Literal[True]


@dataclass(frozen=True, slots=True)
class InvalidDateAnchorError(ValueError):
    """Raised when a memory date anchor is not a supported calendar date."""

    value: str

    def __post_init__(self) -> None:
        """Initialize the validation error without exposing other memory data."""
        ValueError.__init__(
            self,
            "date_anchor must be an ISO date or --MM-DD",
        )


@dataclass(frozen=True, slots=True)
class MissingYearlyDateError(ValueError):
    """Raised when yearly recurrence has no date anchor."""

    def __post_init__(self) -> None:
        """Initialize the validation error."""
        ValueError.__init__(self, "yearly recurrence requires date_anchor")


@dataclass(frozen=True, slots=True)
class YearlessDateRequiresYearlyError(ValueError):
    """Raised when a yearless date is not configured to recur yearly."""

    def __post_init__(self) -> None:
        """Initialize the validation error."""
        ValueError.__init__(self, "yearless date_anchor requires yearly recurrence")


class RememberRequest(BaseModel):
    """Validated memory payload accepted by the remember MCP tool."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: MemoryKind
    content: str
    entity: str | None = None
    date_anchor: str | None = None
    recurrence: MemoryRecurrence = "none"
    lead_days: Annotated[int, Field(ge=0)] = 7


class RememberOptions(TypedDict, total=False):
    """Optional flat MCP arguments accepted by remember."""

    recurrence: MemoryRecurrence
    lead_days: int


def _database_path() -> Path:
    return Path(os.environ.get("PROACTIVE_DATABASE", "~/.proactive-mcp/proactive.db"))


def _memory_item_response(item: MemoryItem) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=item.id,
        kind=item.kind,
        entity=item.entity,
        content=item.content,
        date_anchor=item.date_anchor,
        recurrence=item.recurrence,
        lead_days=item.lead_days,
        source=item.source,
        created_at=item.created_at,
        updated_at=item.updated_at,
        archived=item.archived,
    )


async def remember(
    kind: MemoryKind,
    content: str,
    *,
    entity: str | None = None,
    date_anchor: str | None = None,
    **options: Unpack[RememberOptions],
) -> str:
    """Store a memory item from an agent conversation."""
    memory = RememberRequest(
        kind=kind,
        content=content,
        entity=entity,
        date_anchor=date_anchor,
        recurrence=options.get("recurrence", "none"),
        lead_days=options.get("lead_days", 7),
    )
    _validate_memory_date(memory)
    with Store(_database_path()) as store:
        item = store.remember(
            NewMemory(
                kind=memory.kind,
                content=memory.content,
                entity=memory.entity,
                date_anchor=memory.date_anchor,
                recurrence=memory.recurrence,
                lead_days=memory.lead_days,
                source="agent_conversation",
            )
        )
    return _memory_item_response(item).model_dump_json()


remember.__dict__["__signature__"] = Signature(
    parameters=[
        Parameter("kind", Parameter.POSITIONAL_OR_KEYWORD, annotation=MemoryKind),
        Parameter("content", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
        Parameter(
            "entity",
            Parameter.KEYWORD_ONLY,
            default=None,
            annotation=str | None,
        ),
        Parameter(
            "date_anchor",
            Parameter.KEYWORD_ONLY,
            default=None,
            annotation=str | None,
        ),
        Parameter(
            "recurrence",
            Parameter.KEYWORD_ONLY,
            default="none",
            annotation=MemoryRecurrence,
        ),
        Parameter(
            "lead_days",
            Parameter.KEYWORD_ONLY,
            default=7,
            annotation=Annotated[int, Field(ge=0)],
        ),
    ],
    return_annotation=str,
)


def _validate_memory_date(memory: RememberRequest) -> None:
    value = memory.date_anchor
    if value is None:
        if memory.recurrence == "yearly":
            raise MissingYearlyDateError
        return
    normalized = f"2000-{value.removeprefix('--')}" if value.startswith("--") else value
    try:
        _ = date.fromisoformat(normalized)
    except ValueError:
        raise InvalidDateAnchorError(value) from None
    if value.startswith("--") and memory.recurrence != "yearly":
        raise YearlessDateRequiresYearlyError


async def recall(query: str, kind: MemoryKind | None = None) -> str:
    """Return matching active memory items as a JSON object."""
    with Store(_database_path()) as store:
        items = store.recall(query, kind=kind)
    return RecallResponse(
        items=tuple(_memory_item_response(item) for item in items)
    ).model_dump_json()


async def forget(memory_id: Annotated[int, Field(validation_alias="id")]) -> str:
    """Soft-archive a memory item by id."""
    with Store(_database_path()) as store:
        _ = store.forget(memory_id)
    return ForgetResponse(id=memory_id, archived=True).model_dump_json()


__all__ = [
    "ForgetResponse",
    "MemoryItemResponse",
    "RecallResponse",
    "RememberRequest",
    "forget",
    "recall",
    "remember",
]
