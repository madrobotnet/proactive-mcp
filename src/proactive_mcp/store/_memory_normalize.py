"""Normalization helpers for memory entity labels, aliases, and paths."""

from __future__ import annotations

from unicodedata import normalize

from ._memory_models import (
    INVALID_EMPTY_ENTITY,
    INVALID_EMPTY_PATH,
    INVALID_PATH_DEPTH,
    MemoryValidationError,
)


def normalize_label(label: str) -> str:
    """Return an NFC-stripped entity label."""
    normalized = normalize("NFC", label).strip()
    if not normalized:
        raise MemoryValidationError(*INVALID_EMPTY_ENTITY)
    return normalized


def normalize_alias(alias: str) -> str:
    """Return a casefolded, whitespace-stripped alias key."""
    return "".join(normalize("NFC", alias).casefold().split())


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
