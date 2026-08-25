"""Transactional persistence for authorized Google source setup."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, final

from proactive_mcp.store import (
    ReceiptErasurePendingError,
    Store,
    UnsafeDatabasePathError,
)

if TYPE_CHECKING:
    from pathlib import Path


@final
class GoogleSourceConfigurationError(Exception):
    """Signal that authorized source state could not be safely persisted."""

    def __init__(self) -> None:
        """Expose only a fixed credential-safe recovery message."""
        Exception.__init__(self, "Google setup could not be saved; run setup again")


def persist_google_setup_state(
    database_path: Path,
    store_type: type[Store],
) -> None:
    """Atomically configure both shared sources or expose one safe error."""
    try:
        with store_type(database_path) as store:
            store.set_google_auth_state("configured")
    except (
        OSError,
        sqlite3.Error,
        ReceiptErasurePendingError,
        UnsafeDatabasePathError,
    ):
        raise GoogleSourceConfigurationError from None
