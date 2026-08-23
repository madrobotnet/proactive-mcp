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

MAX_MEMORY_CONTENT_BYTES: Final[int] = 4096
MAX_MEMORY_ENTITY_BYTES: Final[int] = 256
MAX_MEMORY_ENTITY_PATH_BYTES: Final[int] = 512
MAX_MEMORY_DATE_ANCHOR_BYTES: Final[int] = 10
MAX_MEMORY_RECORD_BYTES: Final[int] = 5120
MAX_MEMORY_LEAD_DAYS: Final[int] = 366
MAX_MEMORY_PAGE_SIZE: Final[int] = 100


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
INVALID_LIMIT: Final = (
    "limit",
    f"must be between 1 and {MAX_MEMORY_PAGE_SIZE}",
)
INVALID_RECORD_SIZE: Final = (
    "record",
    f"exceeds {MAX_MEMORY_RECORD_BYTES} UTF-8 bytes",
)
INVALID_LEAD_DAYS: Final = (
    "lead_days",
    f"must be between 0 and {MAX_MEMORY_LEAD_DAYS}",
)
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


def validate_new_memory(memory: NewMemory) -> None:
    """Enforce caller-independent storage bounds before persistence."""
    bounded = (
        ("content", memory.content, MAX_MEMORY_CONTENT_BYTES),
        ("entity", memory.entity, MAX_MEMORY_ENTITY_BYTES),
        ("entity_path", memory.entity_path, MAX_MEMORY_ENTITY_PATH_BYTES),
        ("date_anchor", memory.date_anchor, MAX_MEMORY_DATE_ANCHOR_BYTES),
    )
    for field, value, maximum in bounded:
        if value is not None and len(value.encode("utf-8")) > maximum:
            raise MemoryValidationError(field, f"exceeds {maximum} UTF-8 bytes")
    total_bytes = sum(
        len(value.encode("utf-8"))
        for value in (
            memory.kind,
            memory.content,
            memory.entity or "",
            memory.entity_kind or "",
            memory.entity_path or "",
            memory.attribute,
            memory.date_anchor or "",
            memory.recurrence,
            memory.source,
        )
    )
    if total_bytes > MAX_MEMORY_RECORD_BYTES:
        raise MemoryValidationError(*INVALID_RECORD_SIZE)
    if not 0 <= memory.lead_days <= MAX_MEMORY_LEAD_DAYS:
        raise MemoryValidationError(*INVALID_LEAD_DAYS)


@dataclass(frozen=True, slots=True)
class EntityAliasConflictError(Exception):
    """Raised when a globally normalized alias belongs to another entity."""

    alias: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"entity alias {self.alias!r} is already assigned")
