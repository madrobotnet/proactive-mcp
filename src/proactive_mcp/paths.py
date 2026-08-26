"""Local state file locations shared by every proactive-mcp process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DATABASE_ENV",
    "DEFAULT_DATABASE",
    "ProactivePaths",
    "normalize_state_path",
    "resolve_paths",
]

DATABASE_ENV: Final = "PROACTIVE_DATABASE"
DEFAULT_DATABASE: Final = Path("~/.proactive-mcp/proactive.db")
_CONFIG_NAME: Final = "config.toml"


def normalize_state_path(path: Path) -> Path:
    """Return one absolute lexical identity without resolving symlinks."""
    expanded = path.expanduser()
    candidate = expanded if expanded.is_absolute() else Path.cwd() / expanded
    normalized = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        normalized = normalized.parent if part == ".." else normalized / part
    return normalized


@dataclass(frozen=True, slots=True)
class ProactivePaths:
    """The local state layout of one proactive-mcp installation (§4.2)."""

    database: Path
    config: Path

    @property
    def state_directory(self) -> Path:
        """Return the directory owning the database, config, and credentials."""
        return self.database.parent

    @classmethod
    def for_database(cls, database: Path) -> Self:
        """Derive every state path from one database location."""
        resolved = normalize_state_path(database)
        return cls(database=resolved, config=resolved.parent / _CONFIG_NAME)


def resolve_paths(environ: Mapping[str, str]) -> ProactivePaths:
    """Resolve the state layout from the database environment override."""
    configured = environ.get(DATABASE_ENV)
    return ProactivePaths.for_database(
        DEFAULT_DATABASE if configured is None else Path(configured)
    )
