"""Serialized MCP responses for memory model v2."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from proactive_mcp.store import (  # noqa: TC001
    Entity,
    EntityKind,
    EntityStatus,
    MemoryAttribute,
    MemoryItem,
    MemoryKind,
    MemoryRecurrence,
    MemorySource,
)


class MemoryItemResponse(BaseModel):
    """Serialized memory item returned by MCP memory tools."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    kind: MemoryKind
    entity_id: int | None
    entity: str | None
    entity_kind: EntityKind | None
    entity_path: str | None
    attribute: MemoryAttribute
    content: str
    date_anchor: str | None
    recurrence: MemoryRecurrence
    lead_days: int | None
    source: MemorySource
    created_at: str
    updated_at: str
    archived: bool
    is_contradictory: bool


class EntityResponse(BaseModel):
    """Serialized entity returned by the list_entities MCP tool."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    kind: EntityKind
    path: str | None
    label: str
    status: EntityStatus
    created_at: str
    updated_at: str


class RecallResponse(BaseModel):
    """Serialized recall result containing matching memory items."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: tuple[MemoryItemResponse, ...]


class ListEntitiesResponse(BaseModel):
    """Serialized list of active entities."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: tuple[EntityResponse, ...]


class ForgetResponse(BaseModel):
    """Serialized result of archiving a memory item."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    archived: Literal[True]


def memory_item_response(item: MemoryItem) -> MemoryItemResponse:
    """Build the MCP payload for a stored memory item."""
    return MemoryItemResponse(
        id=item.id,
        kind=item.kind,
        entity_id=item.entity_id,
        entity=item.entity,
        entity_kind=item.entity_kind,
        entity_path=item.entity_path,
        attribute=item.attribute,
        content=item.content,
        date_anchor=item.date_anchor,
        recurrence=item.recurrence,
        lead_days=item.lead_days,
        source=item.source,
        created_at=item.created_at,
        updated_at=item.updated_at,
        archived=item.archived,
        is_contradictory=item.is_contradictory,
    )


def entity_response(entity: Entity) -> EntityResponse:
    """Build the MCP payload for a stored entity."""
    return EntityResponse(
        id=entity.id,
        kind=entity.kind,
        path=entity.path,
        label=entity.label,
        status=entity.status,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


__all__ = [
    "EntityResponse",
    "ForgetResponse",
    "ListEntitiesResponse",
    "MemoryItemResponse",
    "RecallResponse",
    "entity_response",
    "memory_item_response",
]
