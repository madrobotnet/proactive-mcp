from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING

from proactive_mcp.store import LazySyncLease, Store
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_LEASE_DURATION = timedelta(minutes=5)


def _acquire_at_barrier(
    database: Path,
    now: datetime,
    barrier: Barrier,
) -> LazySyncLease | None:
    clock = FakeClock(now)
    with Store(database, clock=clock) as store:
        assert barrier.wait(timeout=10) >= 0
        return store.acquire_lazy_sync_lease(lease_duration=_LEASE_DURATION)


def test_lazy_sync_lease_has_one_atomic_winner(tmp_path: Path) -> None:
    # Given: two connections released against one empty lease row.
    database = tmp_path / "db"
    now = utc_datetime(2026, 8, 21, 16)
    barrier = Barrier(2)

    # When: both reserve at the same instant.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(_acquire_at_barrier, database, now, barrier)
            for _ in range(2)
        )
    leases = tuple(future.result(timeout=10) for future in futures)

    # Then: SQLite grants exactly one reservation before remote I/O can begin.
    assert sum(lease is not None for lease in leases) == 1


def test_lazy_sync_lease_release_and_expiry_are_token_fenced(tmp_path: Path) -> None:
    # Given: one lease expires and is replaced by another process.
    clock = FakeClock(utc_datetime(2026, 8, 21, 16))
    database = tmp_path / "db"
    with (
        Store(database, clock=clock) as first,
        Store(database, clock=clock) as successor,
    ):
        expired = first.acquire_lazy_sync_lease(lease_duration=_LEASE_DURATION)
        assert expired is not None
        clock.advance(_LEASE_DURATION)
        replacement = successor.acquire_lazy_sync_lease(lease_duration=_LEASE_DURATION)
        assert replacement is not None

        # When: the expired owner releases after the successor took over.
        stale_release = first.release_lazy_sync_lease(expired)
        still_reserved = first.acquire_lazy_sync_lease(lease_duration=_LEASE_DURATION)
        current_release = successor.release_lazy_sync_lease(replacement)
        available = first.acquire_lazy_sync_lease(lease_duration=_LEASE_DURATION)

    # Then: stale release cannot clear the successor, but explicit release can.
    assert stale_release is False
    assert still_reserved is None
    assert current_release is True
    assert available is not None
