"""Validated MCP request payloads for memory model v2."""

from __future__ import annotations

from typing import Annotated, ClassVar, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from proactive_mcp.store import (
    MAX_MEMORY_LEAD_DAYS,
    EntityKind,
    MemoryAttribute,
    MemoryKind,
    MemoryRecurrence,
    NewMemory,
)


class RememberRequest(BaseModel):
    """Validated memory payload accepted by the remember MCP tool."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: MemoryKind
    content: str
    entity: str | None = None
    entity_kind: EntityKind | None = None
    entity_path: str | None = None
    attribute: MemoryAttribute = "free"
    date_anchor: str | None = None
    recurrence: MemoryRecurrence = "none"
    lead_days: Annotated[int, Field(ge=0, le=MAX_MEMORY_LEAD_DAYS)] = 7


class UpdateRequest(RememberRequest):
    """Validated replacement payload accepted by the update MCP tool."""


class MemoryOptions(TypedDict, total=False):
    """Optional flat MCP arguments accepted by remember and update."""

    entity_kind: EntityKind | None
    entity_path: str | None
    attribute: MemoryAttribute
    date_anchor: str | None
    recurrence: MemoryRecurrence
    lead_days: int


def new_memory(memory: RememberRequest) -> NewMemory:
    """Convert a validated remember payload into a persistable memory."""
    return NewMemory(
        kind=memory.kind,
        content=memory.content,
        entity=memory.entity,
        entity_kind=memory.entity_kind,
        entity_path=memory.entity_path,
        attribute=memory.attribute,
        date_anchor=memory.date_anchor,
        recurrence=memory.recurrence,
        lead_days=memory.lead_days,
        source="agent_conversation",
    )


__all__ = [
    "MemoryOptions",
    "RememberRequest",
    "UpdateRequest",
    "new_memory",
]
