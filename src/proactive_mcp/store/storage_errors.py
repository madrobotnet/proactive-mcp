"""Typed storage safety errors shared by platform backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from pathlib import Path


@final
class ReceiptErasurePendingError(Exception):
    """Raised when an older reader prevents migrated receipt erasure."""

    def __init__(self) -> None:
        """Expose only a fixed credential-safe message."""
        Exception.__init__(
            self,
            "receipt erasure is blocked; close older processes and retry",
        )


@dataclass(slots=True)
class UnsafeDatabasePathError(Exception):
    """Raised when the database path could expose or redirect private data."""

    path: Path
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a non-sensitive message."""
        Exception.__init__(
            self,
            "database path is unsafe; choose a private user-owned directory and retry",
        )


__all__ = ["ReceiptErasurePendingError", "UnsafeDatabasePathError"]
