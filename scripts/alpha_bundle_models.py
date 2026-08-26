"""Typed contracts shared by the alpha bundle builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class BuildConfig:
    """Project and output roots for one bundle build."""

    project_root: Path
    output_directory: Path


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One bounded subprocess step in the bundle build."""

    step: str
    argv: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True, slots=True)
class WheelSource:
    """One locked wheel download and expected digest."""

    url: str
    filename: str
    sha256: str


class BuildError(Exception):
    """Signal a machine-readable bundle build failure."""

    def __init__(self, *, reason: str) -> None:
        """Store the bounded build failure reason."""
        super().__init__(reason)
        self.reason: Final = reason


class ManifestError(BuildError):
    """Signal a malformed or mismatched wheel manifest."""
