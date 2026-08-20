"""Typed storage safety errors shared by platform backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class UnsafeDatabasePathError(Exception):
    """Raised when the database path could expose or redirect private data."""

    path: Path
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a non-sensitive message."""
        Exception.__init__(self, f"unsafe database path: {self.reason}")


__all__ = ["UnsafeDatabasePathError"]
