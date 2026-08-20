"""Packaged SQL migration resources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Final

_MIGRATION_NAME: Final[re.Pattern[str]] = re.compile(r"^(\d+)_[A-Za-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class MissingMigrationResourcesError(Exception):
    """Raised when packaged SQL migration files cannot be loaded."""


def load_migrations() -> tuple[tuple[int, str], ...]:
    """Return packaged (version, sql) pairs in version order."""
    package = __package__
    if package is None:
        raise MissingMigrationResourcesError
    loaded: list[tuple[int, str]] = []
    for resource in files(package).iterdir():
        matched = _MIGRATION_NAME.match(resource.name)
        if matched is None:
            continue
        loaded.append((int(matched.group(1)), resource.read_text(encoding="utf-8")))
    if not loaded:
        raise MissingMigrationResourcesError
    loaded.sort()
    return tuple(loaded)
