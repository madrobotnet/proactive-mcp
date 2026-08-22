"""Atomic cross-process reservation for degraded lazy source reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from ._sqlite_transaction import ImmediateTransaction

if TYPE_CHECKING:
    import sqlite3

    from proactive_mcp.clock import Clock

__all__ = ["InvalidLazySyncLeaseDurationError", "LazySyncLease", "LazySyncLeaseStore"]

_ACQUIRE: Final = """
    INSERT INTO lazy_sync_lease (id, owner_token, acquired_at, expires_at)
    VALUES (1, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        owner_token = excluded.owner_token,
        acquired_at = excluded.acquired_at,
        expires_at = excluded.expires_at
    WHERE lazy_sync_lease.expires_at <= excluded.acquired_at
    """
_RELEASE: Final = """
    DELETE FROM lazy_sync_lease
    WHERE id = 1 AND owner_token = ?
    """


@dataclass(frozen=True, slots=True)
class InvalidLazySyncLeaseDurationError(ValueError):
    """Raised when an expiring lease was given a non-positive duration."""

    def __post_init__(self) -> None:
        """Initialize the stable boundary-safe message."""
        ValueError.__init__(self, "lazy-sync lease duration must be positive")


@dataclass(frozen=True, slots=True)
class LazySyncLease:
    """An opaque ownership fence for one in-flight remote source attempt."""

    token: str
    acquired_at: datetime
    expires_at: datetime


class LazySyncLeaseStore:
    """Serialize lazy source reads without holding a transaction over I/O."""

    _connection: sqlite3.Connection
    _clock: Clock

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._connection = connection
        self._clock = clock

    def acquire(self, *, lease_duration: timedelta) -> LazySyncLease | None:
        """Acquire the lease atomically, replacing it only after inclusive expiry."""
        if lease_duration <= timedelta(0):
            raise InvalidLazySyncLeaseDurationError
        acquired_at = self._clock.now().astimezone(UTC)
        expires_at = acquired_at + lease_duration
        lease = LazySyncLease(
            token=uuid4().hex,
            acquired_at=acquired_at,
            expires_at=expires_at,
        )
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(
                _ACQUIRE,
                (
                    lease.token,
                    acquired_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            return lease if cursor.rowcount == 1 else None

    def release(self, lease: LazySyncLease) -> bool:
        """Release only the matching lease; stale owners cannot clear successors."""
        with ImmediateTransaction(self._connection):
            cursor = self._connection.execute(_RELEASE, (lease.token,))
            return cursor.rowcount == 1
