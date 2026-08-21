"""Typed memory records, entity records, and validation errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import TypeAdapter

EntityKind = Literal["person", "place", "org", "thing", "activity"]
EntityStatus = Literal["active", "merged", "archived"]
MemoryKind = Literal["fact", "commitment", "preference", "note"]
MemoryAttribute = Literal["birthday", "anniversary", "deadline", "relationship", "free"]
MemoryRecurrence = Literal["none", "yearly"]
MemorySource = Literal["agent_conversation", "manual"]


@dataclass(frozen=True, slots=True)
class NewMemory:
    """A typed memory item ready to persist."""

    kind: MemoryKind
    content: str
    entity: str | None = None
    entity_kind: EntityKind | None = None
    entity_path: str | None = None
    attribute: MemoryAttribute = "free"
    date_anchor: str | None = None
    recurrence: MemoryRecurrence = "none"
    lead_days: int = 7
    source: MemorySource = "agent_conversation"


@dataclass(frozen=True, slots=True)
class Entity:
    """A stored entity available for memory classification."""

    id: int
    kind: EntityKind
    path: str | None
    label: str
    status: EntityStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """A stored memory item."""

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


MEMORY_ITEM_ADAPTER: Final[TypeAdapter[MemoryItem]] = TypeAdapter(MemoryItem)
ENTITY_ADAPTER: Final[TypeAdapter[Entity]] = TypeAdapter(Entity)
INVALID_LIMIT: Final = ("limit", "must be at least 1")
INVALID_DUPLICATE_DATE: Final = ("date_anchor", "duplicates an active dated fact")
INVALID_ENTITY_METADATA: Final = ("entity", "is required for entity metadata")
INVALID_ENTITY_KIND: Final = ("entity_kind", "is required when entity is set")
INVALID_ALIAS_TARGET: Final = ("entity", "alias target does not exist")
INVALID_ALIAS_KIND: Final = ("entity_kind", "does not match the alias entity")
INVALID_CREATED_ENTITY: Final = ("entity", "was not created")
INVALID_DATABASE_RESULT: Final = ("database", "expected an integer result")
INVALID_EMPTY_ENTITY: Final = ("entity", "must not be empty")
INVALID_PATH_DEPTH: Final = ("entity_path", "must have between 1 and 3 segments")
INVALID_EMPTY_PATH: Final = ("entity_path", "must not contain empty segments")


@dataclass(frozen=True, slots=True)
class MemoryNotFoundError(Exception):
    """Raised when a memory item does not exist."""

    id: int

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"memory item {self.id} not found")


@dataclass(frozen=True, slots=True)
class MemoryValidationError(Exception):
    """Raised when a memory value cannot be represented by the v2 model."""

    field: str
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"invalid memory {self.field}: {self.reason}")


@dataclass(frozen=True, slots=True)
class EntityAliasConflictError(Exception):
    """Raised when a globally normalized alias belongs to another entity."""

    alias: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"entity alias {self.alias!r} is already assigned")
