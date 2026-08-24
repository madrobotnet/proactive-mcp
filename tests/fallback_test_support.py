"""Shared runners and aged-store fixtures for fallback dispatcher tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from proactive_mcp.delivery.fallback import FallbackDispatcher
from proactive_mcp.delivery.notify import NotificationError, NotificationHost
from proactive_mcp.store import DeliveryClaim, Detection, SituationEvidence, Store
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from datetime import datetime
    from pathlib import Path
    from threading import Barrier

    from proactive_mcp.config import FallbackSettings
    from proactive_mcp.delivery.fallback import FallbackDispatch
    from proactive_mcp.delivery.notify import (
        NotificationErrorCode,
        NotificationRunner,
    )
    from proactive_mcp.store import Situation, SituationPriority

NOW = utc_datetime(2026, 8, 21, 16)
WAIT = timedelta(minutes=30)
SECOND = timedelta(seconds=1)
SEOUL = ZoneInfo("Asia/Seoul")
NOTIFY = ("/usr/bin/notify-send", "--")
POISON_TITLE = "Alice Johnson"
CANARY_SUBJECT = "CANARY_SUBJECT_Q3-layoff-list"
CANARY_SENDER = "CANARY_SENDER_alice.secret@corp.example"


class RecordingRunner:
    """Collect argv vectors and timeouts; mutation is the test probe."""

    calls: list[tuple[str, ...]]
    timeouts: list[timedelta]

    def __init__(self) -> None:
        self.calls = []
        self.timeouts = []

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        self.calls.append(tuple(argv))
        self.timeouts.append(timeout)


class FailingRunner:
    """Fail every send; mutation records the attempts."""

    recorder: RecordingRunner
    error_code: NotificationErrorCode

    def __init__(self, error_code: NotificationErrorCode) -> None:
        self.recorder = RecordingRunner()
        self.error_code = error_code

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        self.recorder.run(argv, timeout)
        raise NotificationError(self.error_code)


class ProbingRunner:
    """Dispatch from a second connection mid-send; mutation records it."""

    recorder: RecordingRunner
    store: Store
    concurrent: list[FallbackDispatch]

    def __init__(self, store: Store) -> None:
        self.recorder = RecordingRunner()
        self.store = store
        self.concurrent = []

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        self.recorder.run(argv, timeout)
        self.concurrent.extend(dispatch(self.store, RecordingRunner()))


def detection(
    key: str,
    *,
    priority: SituationPriority = "critical",
    expires_at: datetime | None = None,
) -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=key,
        priority=priority,
        title=f"Calendar conflict {key}",
        why_now="Fixture starts within two hours",
        evidence=SituationEvidence(facts={"event_a_id": key}),
        expires_at=expires_at,
    )


def poisoned() -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key="poisoned",
        priority="critical",
        title=POISON_TITLE,
        why_now=f"overlap involving {CANARY_SUBJECT}",
        evidence=SituationEvidence(
            facts={"event_a_id": "evt-a"},
            quoted_external={
                "subject": CANARY_SUBJECT,
                "sender": CANARY_SENDER,
            },
        ),
    )


@contextmanager
def aged(
    path: Path,
    *detections: Detection,
    age: timedelta = WAIT,
) -> Generator[Store]:
    """Yield a store whose detections were detected ``age`` before now."""
    clock = FakeClock(NOW - age)
    with Store(path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections(detections)
        clock.set(NOW)
        yield store


def dispatch(
    store: Store,
    runner: NotificationRunner,
    settings: FallbackSettings | None = None,
) -> tuple[FallbackDispatch, ...]:
    dispatcher = FallbackDispatcher(
        store.fallbacks,
        NotificationHost("linux", runner),
        settings,
    )
    return dispatcher.dispatch(NOW)


def claimed(store: Store) -> tuple[str, ...]:
    """Return the dedupe key of every situation holding a fallback record."""
    return tuple(
        item.dedupe_key
        for item in store.situations.list_situations()
        if store.fallbacks.history(item.id) is not None
    )


def by_key(store: Store, key: str) -> Situation:
    found = tuple(
        item for item in store.situations.list_situations() if item.dedupe_key == key
    )
    assert len(found) == 1
    return found[0]


def delivery(now: datetime) -> DeliveryClaim:
    return DeliveryClaim(
        delivered_at=now.isoformat(),
        cooldown_after=(now - timedelta(hours=24)).isoformat(),
        local_day_start=(now - timedelta(hours=16)).isoformat(),
        local_day_end=(now + timedelta(hours=8)).isoformat(),
        daily_budget=4,
        allow_noncritical=True,
    )


def dispatch_in_worker(path: Path, barrier: Barrier) -> tuple[int, int]:
    runner = RecordingRunner()
    with Store(path / "db", clock=FakeClock(NOW)) as store:
        assert barrier.wait(timeout=10) >= 0
        dispatched = dispatch(store, runner)
    return (len(dispatched), len(runner.calls))
