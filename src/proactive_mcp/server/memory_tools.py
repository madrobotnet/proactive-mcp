"""MCP tool implementations for memory model v2."""

from __future__ import annotations

import os
from inspect import Parameter, Signature
from typing import TYPE_CHECKING, Annotated, Unpack

from pydantic import Field

from proactive_mcp.paths import resolve_paths
from proactive_mcp.server.memory_requests import (
    MemoryOptions,
    RememberRequest,
    UpdateRequest,
    new_memory,
)
from proactive_mcp.server.memory_responses import (
    EntityResponse,
    ForgetResponse,
    ListEntitiesResponse,
    MemoryItemResponse,
    RecallResponse,
    entity_response,
    memory_item_response,
)
from proactive_mcp.store import (
    MAX_MEMORY_LEAD_DAYS,
    MAX_MEMORY_PAGE_SIZE,
    EntityKind,
    MemoryAttribute,
    MemoryKind,
    MemoryRecurrence,
    Store,
)

if TYPE_CHECKING:
    from pathlib import Path


def _database_path() -> Path:
    return resolve_paths(os.environ).database


async def remember(
    kind: MemoryKind,
    content: str,
    *,
    entity: str | None = None,
    **options: Unpack[MemoryOptions],
) -> str:
    """Store a memory item from an agent conversation."""
    memory = RememberRequest(kind=kind, content=content, entity=entity, **options)
    candidate = new_memory(memory)
    with Store(_database_path()) as store:
        item = store.remember(candidate)
    return memory_item_response(item).model_dump_json()


remember.__dict__["__signature__"] = Signature(
    parameters=[
        Parameter("kind", Parameter.POSITIONAL_OR_KEYWORD, annotation=MemoryKind),
        Parameter("content", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
        Parameter(
            "entity", Parameter.KEYWORD_ONLY, default=None, annotation=str | None
        ),
        Parameter(
            "entity_kind",
            Parameter.KEYWORD_ONLY,
            default=None,
            annotation=EntityKind | None,
        ),
        Parameter(
            "entity_path", Parameter.KEYWORD_ONLY, default=None, annotation=str | None
        ),
        Parameter(
            "attribute",
            Parameter.KEYWORD_ONLY,
            default="free",
            annotation=MemoryAttribute,
        ),
        Parameter(
            "date_anchor", Parameter.KEYWORD_ONLY, default=None, annotation=str | None
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
            annotation=Annotated[int, Field(ge=0, le=MAX_MEMORY_LEAD_DAYS)],
        ),
    ],
    return_annotation=str,
)


async def recall(
    query: str,
    *,
    kind: MemoryKind | None = None,
    entity_kind: EntityKind | None = None,
    path_prefix: str | None = None,
    limit: Annotated[int, Field(ge=1, le=MAX_MEMORY_PAGE_SIZE)] = 20,
) -> str:
    """Return matching active memory items as a JSON object."""
    with Store(_database_path()) as store:
        items = store.recall(
            query,
            kind=kind,
            entity_kind=entity_kind,
            path_prefix=path_prefix,
            limit=limit,
        )
    return RecallResponse(
        items=tuple(memory_item_response(item) for item in items)
    ).model_dump_json()


async def update(
    memory_id: Annotated[int, Field(validation_alias="id")],
    kind: MemoryKind,
    content: str,
    *,
    entity: str | None = None,
    **options: Unpack[MemoryOptions],
) -> str:
    """Replace a memory item's mutable values while retaining its id."""
    memory = UpdateRequest(kind=kind, content=content, entity=entity, **options)
    candidate = new_memory(memory)
    with Store(_database_path()) as store:
        item = store.update(memory_id, candidate)
    return memory_item_response(item).model_dump_json()


update.__dict__["__signature__"] = Signature(
    parameters=[
        Parameter(
            "memory_id",
            Parameter.POSITIONAL_OR_KEYWORD,
            annotation=Annotated[int, Field(validation_alias="id")],
        ),
        Parameter("kind", Parameter.POSITIONAL_OR_KEYWORD, annotation=MemoryKind),
        Parameter("content", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
        Parameter(
            "entity", Parameter.KEYWORD_ONLY, default=None, annotation=str | None
        ),
        Parameter(
            "entity_kind",
            Parameter.KEYWORD_ONLY,
            default=None,
            annotation=EntityKind | None,
        ),
        Parameter(
            "entity_path", Parameter.KEYWORD_ONLY, default=None, annotation=str | None
        ),
        Parameter(
            "attribute",
            Parameter.KEYWORD_ONLY,
            default="free",
            annotation=MemoryAttribute,
        ),
        Parameter(
            "date_anchor", Parameter.KEYWORD_ONLY, default=None, annotation=str | None
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
            annotation=Annotated[int, Field(ge=0, le=MAX_MEMORY_LEAD_DAYS)],
        ),
    ],
    return_annotation=str,
)


async def list_entities(
    *,
    kind: EntityKind | None = None,
    path_prefix: str | None = None,
    after_id: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=MAX_MEMORY_PAGE_SIZE)] = 20,
) -> str:
    """Return active entities as a JSON object."""
    with Store(_database_path()) as store:
        entities = store.list_entities(
            kind=kind,
            path_prefix=path_prefix,
            after_id=after_id,
            limit=limit,
        )
    return ListEntitiesResponse(
        items=tuple(entity_response(entity) for entity in entities),
        next_after_id=entities[-1].id if len(entities) == limit and entities else None,
    ).model_dump_json()


async def forget(memory_id: Annotated[int, Field(validation_alias="id")]) -> str:
    """Soft-archive a memory item by id."""
    with Store(_database_path()) as store:
        _ = store.forget(memory_id)
    return ForgetResponse(id=memory_id, archived=True).model_dump_json()


__all__ = [
    "EntityResponse",
    "ForgetResponse",
    "ListEntitiesResponse",
    "MemoryItemResponse",
    "RecallResponse",
    "RememberRequest",
    "UpdateRequest",
    "forget",
    "list_entities",
    "recall",
    "remember",
    "update",
]
