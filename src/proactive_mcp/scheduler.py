"""Deterministic wait scheduling for the continuous watcher loop."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Final, Protocol

__all__ = ["EventScheduler", "Scheduler", "remaining_delay"]

_NO_DELAY: Final = timedelta()


class Scheduler(Protocol):
    """Wait between watcher passes and report whether to keep running."""

    def wait(self, delay: timedelta) -> bool:
        """Wait for ``delay`` and return whether another pass should run."""
        ...


class EventScheduler:
    """Wait on a settable event so a stop request interrupts the delay.

    Mutation is this class's purpose: ``stop`` is called from a signal
    handler or another thread while ``wait`` is blocking.
    """

    _stop: threading.Event

    def __init__(self) -> None:
        """Create a scheduler whose waits have not been interrupted."""
        self._stop = threading.Event()

    def wait(self, delay: timedelta) -> bool:
        """Wait up to ``delay``, returning False once a stop was requested."""
        return not self._stop.wait(delay.total_seconds())

    def stop(self) -> None:
        """Interrupt the current wait and end the loop after it returns."""
        self._stop.set()


def remaining_delay(interval: timedelta, elapsed: timedelta) -> timedelta:
    """Return the wait that holds a fixed cadence, never a negative delay."""
    return max(interval - elapsed, _NO_DELAY)
