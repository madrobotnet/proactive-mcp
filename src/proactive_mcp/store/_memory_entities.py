"""Entity lookup, creation, and listing for the memory store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._memory_models import (
    INVALID_ALIAS_KIND,
    INVALID_ALIAS_TARGET,
    INVALID_CREATED_ENTITY,
    INVALID_ENTITY_KIND,
    INVALID_ENTITY_METADATA,
    Entity,
    EntityAliasConflictError,
    EntityKind,
    MemoryValidationError,
    NewMemory,
)
from ._memory_normalize import (
    entity_aliases,
    normalize_alias,
    normalize_label,
    normalize_path,
    prefix_filter,
)
from ._memory_sql import (
    INSERT_ENTITY,
    INSERT_ENTITY_ALIAS,
    LAST_INSERT_ROWID,
    SELECT_ACTIVE_ENTITIES,
    SELECT_ENTITY_ID_BY_ALIAS,
)

if TYPE_CHECKING:
    from ._memory_queries import MemoryQueries


def list_active_entities(
    queries: MemoryQueries,
    *,
    kind: EntityKind | None = None,
    path_prefix: str | None = None,
    after_id: int = 0,
    limit: int,
) -> tuple[Entity, ...]:
    """List active entities in stable kind, path, label, and id order."""
    normalized_prefix, prefix_pattern = prefix_filter(path_prefix)
    return queries.capture_entities(
        SELECT_ACTIVE_ENTITIES,
        (
            after_id,
            after_id,
            kind,
            kind,
            normalized_prefix,
            normalized_prefix,
            prefix_pattern,
            limit,
        ),
    )


def resolve_entity(
    queries: MemoryQueries,
    memory: NewMemory,
    timestamp: str,
) -> Entity | None:
    """Resolve or create the entity referenced by a memory item."""
    if memory.entity is None:
        if memory.entity_kind is not None or memory.entity_path is not None:
            raise MemoryValidationError(*INVALID_ENTITY_METADATA)
        return None
    label = normalize_label(memory.entity)
    if memory.entity_kind is None:
        raise MemoryValidationError(*INVALID_ENTITY_KIND)
    path = (
        normalize_path(memory.entity_path) if memory.entity_path is not None else None
    )
    alias_norm = normalize_alias(label)
    entity_id = queries.query_optional_int(
        SELECT_ENTITY_ID_BY_ALIAS,
        (alias_norm,),
    )
    if entity_id is not None:
        entity = queries.entity_by_id(entity_id)
        if entity is None:
            raise MemoryValidationError(*INVALID_ALIAS_TARGET)
        if entity.kind != memory.entity_kind:
            raise MemoryValidationError(*INVALID_ALIAS_KIND)
        return entity

    return _create_entity(queries, memory, label, path, timestamp)


def _create_entity(
    queries: MemoryQueries,
    memory: NewMemory,
    label: str,
    path: str | None,
    timestamp: str,
) -> Entity:
    entity_kind = memory.entity_kind
    if entity_kind is None:
        raise MemoryValidationError(*INVALID_ENTITY_KIND)
    aliases = entity_aliases(label, path)
    for alias in aliases:
        assigned_id = queries.query_optional_int(
            SELECT_ENTITY_ID_BY_ALIAS,
            (normalize_alias(alias),),
        )
        if assigned_id is not None:
            raise EntityAliasConflictError(alias)
    queries.execute(
        INSERT_ENTITY,
        (entity_kind, path, label, timestamp, timestamp),
    )
    new_entity_id = queries.query_int(LAST_INSERT_ROWID)
    for alias in aliases:
        queries.execute(
            INSERT_ENTITY_ALIAS,
            (
                new_entity_id,
                alias,
                normalize_alias(alias),
                memory.source,
                timestamp,
            ),
        )
    entity = queries.entity_by_id(new_entity_id)
    if entity is None:
        raise MemoryValidationError(*INVALID_CREATED_ENTITY)
    return entity
