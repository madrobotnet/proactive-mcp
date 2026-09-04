"""Normalization helpers for memory entity labels, aliases, and paths."""

from __future__ import annotations

from typing import Protocol, TypeAlias
from unicodedata import normalize

from ._memory_models import (
    INVALID_EMPTY_ENTITY,
    INVALID_EMPTY_PATH,
    INVALID_PATH_DEPTH,
    MemoryKind,
    MemoryRecurrence,
    MemoryValidationError,
)

FreeDatedMemoryKey: TypeAlias = tuple[
    MemoryKind,
    int | None,
    str | None,
    MemoryRecurrence,
    str,
]


class FreeDatedMemory(Protocol):
    """Values that define a free dated memory's canonical identity."""

    @property
    def kind(self) -> MemoryKind:
        """Return the memory kind."""
        ...

    @property
    def content(self) -> str:
        """Return the original memory content."""
        ...

    @property
    def date_anchor(self) -> str | None:
        """Return the dated memory anchor."""
        ...

    @property
    def recurrence(self) -> MemoryRecurrence:
        """Return the recurrence policy."""
        ...


def normalize_label(label: str) -> str:
    """Return an NFC-stripped entity label."""
    normalized = normalize("NFC", label).strip()
    if not normalized:
        raise MemoryValidationError(*INVALID_EMPTY_ENTITY)
    return normalized


def normalize_alias(alias: str) -> str:
    """Return a casefolded, whitespace-stripped alias key."""
    return "".join(normalize("NFC", alias).casefold().split())


def normalize_memory_content(content: str) -> str:
    """Return the canonical content component of a dated memory identity."""
    return " ".join(normalize("NFC", content).casefold().split())


def free_dated_memory_key(
    memory: FreeDatedMemory,
    entity_id: int | None,
) -> FreeDatedMemoryKey:
    """Return the canonical identity of one free dated memory."""
    return (
        memory.kind,
        entity_id,
        memory.date_anchor,
        memory.recurrence,
        normalize_memory_content(memory.content),
    )


def entity_aliases(label: str, path: str | None) -> tuple[str, ...]:
    """Return unique aliases for an entity label and optional path leaf."""
    if path is None:
        return (label,)
    leaf = path.rsplit("/", maxsplit=1)[-1]
    if normalize_alias(label) == normalize_alias(leaf):
        return (label,)
    return (label, leaf)


def normalize_path(path: str) -> str:
    """Return an NFC-stripped path with 1 to 3 non-empty segments."""
    max_depth = 3
    segments = path.split("/")
    if not 1 <= len(segments) <= max_depth:
        raise MemoryValidationError(*INVALID_PATH_DEPTH)
    normalized_segments = tuple(
        normalize("NFC", segment).strip() for segment in segments
    )
    if any(not segment for segment in normalized_segments):
        raise MemoryValidationError(*INVALID_EMPTY_PATH)
    return "/".join(normalized_segments)


def escape_like(query: str) -> str:
    """Escape LIKE wildcards and the escape character."""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def prefix_filter(path_prefix: str | None) -> tuple[str | None, str | None]:
    """Return a normalized path prefix and its LIKE child-path pattern."""
    normalized_prefix = normalize_path(path_prefix) if path_prefix is not None else None
    prefix_pattern = (
        f"{escape_like(normalized_prefix)}/%" if normalized_prefix is not None else None
    )
    return normalized_prefix, prefix_pattern
