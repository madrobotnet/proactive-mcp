from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from proactive_mcp.delivery.evaluation import (
    EvaluationDependencies,
    EvaluationPass,
    EvaluationService,
    SkippedSources,
)
from proactive_mcp.situations.engine import EvaluationResult, SituationEngine
from proactive_mcp.situations.inputs import (
    EngineInputs,
    InboxThreadSnapshot,
    SourceSnapshot,
)
from proactive_mcp.sources.credentials import GOOGLE_READONLY_SCOPES
from proactive_mcp.store import NewMemory, SourceFreshness

if TYPE_CHECKING:
    from datetime import datetime

    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery.evaluation import SourceOutcome, SourceSkipReason
    from proactive_mcp.sources.calendar import CalendarEvent
    from proactive_mcp.sources.credentials import GoogleCredential
    from proactive_mcp.store import Store
    from tests.situation_test_support import FakeClock

_ABSENT_FRESHNESS = SourceFreshness(
    status="not_configured",
    last_success_at=None,
    last_attempt_at=None,
    age_seconds=None,
    error_code=None,
)


@dataclass(frozen=True, slots=True)
class RecordingHeartbeat:
    """Record the ordered daemon liveness calls of one watcher run."""

    events: list[str] = field(default_factory=list)

    def record_start(self, pid: int) -> None:
        self.events.append(f"start:{pid}")

    def record_heartbeat(self) -> None:
        self.events.append("heartbeat")

    def record_stop(self) -> None:
        self.events.append("stop")


@dataclass(frozen=True, slots=True)
class RecordingScheduler:
    """Record every requested wait and end the loop after a wait count."""

    stop_after: int
    waits: list[timedelta] = field(default_factory=list)

    def wait(self, delay: timedelta) -> bool:
        self.waits.append(delay)
        return len(self.waits) < self.stop_after


@dataclass(frozen=True, slots=True)
class FakeEvaluationRunner:
    """Return one prepared pass result and consume fake pass time."""

    result: EvaluationPass
    clock: FakeClock
    duration: timedelta = timedelta()
    passes: list[datetime] = field(default_factory=list)

    def run_once(self) -> EvaluationPass:
        self.passes.append(self.clock.now())
        self.clock.advance(self.duration)
        return self.result


@dataclass(frozen=True, slots=True)
class RecordedNotification:
    """One situation this fake reported as notified."""

    situation_id: int


@dataclass(frozen=True, slots=True)
class RecordingNotifier:
    """Notify a fixed situation set and record every dispatch instant."""

    situation_ids: tuple[int, ...] = ()
    dispatches: list[datetime] = field(default_factory=list)

    def dispatch(self, now: datetime) -> tuple[RecordedNotification, ...]:
        self.dispatches.append(now)
        return tuple(
            RecordedNotification(situation_id=situation_id)
            for situation_id in self.situation_ids
        )


@dataclass(frozen=True, slots=True)
class SkippedSourceProvider:
    """Perform no remote read and always name the same skip reason."""

    reason: SourceSkipReason

    def prepare_sources(self) -> SourceOutcome:
        return SkippedSources(self.reason)


@dataclass(frozen=True, slots=True)
class StoreBackedReader:
    """Reserve real generations and return fixture snapshots as one read."""

    store: Store
    threads: tuple[InboxThreadSnapshot, ...] = ()
    events: tuple[CalendarEvent, ...] = ()
    reads: list[int] = field(default_factory=list)

    def prepare_evaluation(self) -> EngineInputs:
        self.reads.append(len(self.reads) + 1)
        return EngineInputs(
            gmail_threads=SourceSnapshot(
                generation=self.store.reserve_source_generation("gmail"),
                items=self.threads,
            ),
            calendar_events=SourceSnapshot(
                generation=self.store.reserve_source_generation("calendar"),
                items=self.events,
            ),
        )


@dataclass(frozen=True, slots=True)
class FakeCredential:
    """A non-secret stand-in for the stored read-only Google credential."""

    @property
    def refresh_token(self) -> str:
        return "redacted"

    @property
    def scopes(self) -> tuple[str, str]:
        return GOOGLE_READONLY_SCOPES

    def to_json(self) -> str:
        return "{}"


@dataclass(frozen=True, slots=True)
class FakeCredentialStore:
    """Load the credential this fake was constructed with, if any."""

    credential: GoogleCredential | None = None

    def load(self) -> GoogleCredential | None:
        return self.credential


@dataclass(frozen=True, slots=True)
class FakeReaderFactory:
    """Open the prepared reader and count credential-backed openings."""

    reader: StoreBackedReader
    openings: list[int] = field(default_factory=list)

    def open(self, credential: GoogleCredential) -> StoreBackedReader:
        del credential
        self.openings.append(len(self.openings) + 1)
        return self.reader


def local_only_pass() -> EvaluationPass:
    """Build a source-skipped pass result for watcher loop-shape tests."""
    result = EvaluationResult(
        created=0,
        reactivated=0,
        refreshed=0,
        resolved=0,
        expired=0,
        woken=0,
        warnings=("gmail: skipped this pass (no snapshot); situations kept",),
        gmail_freshness=_ABSENT_FRESHNESS,
        calendar_freshness=_ABSENT_FRESHNESS,
    )
    return EvaluationPass(
        result=result,
        sources=SkippedSources("missing_credentials"),
        warnings=result.warnings,
    )


def open_local_evaluation(store: Store, clock: Clock) -> EvaluationService:
    """Wire the shared evaluation service to local memories only."""
    return EvaluationService(
        EvaluationDependencies(
            evaluator=SituationEngine(store, clock, UTC),
            sources=SkippedSourceProvider("missing_credentials"),
        )
    )


def birthday_memory() -> NewMemory:
    """Return the Mother's Birthday acceptance fixture memory (§11.3)."""
    return NewMemory(
        kind="fact",
        entity="Mother",
        entity_kind="person",
        attribute="birthday",
        content="Fixture birthday",
        date_anchor="--07-18",
        recurrence="yearly",
        lead_days=7,
    )


def stale_reply_thread(now: datetime) -> InboxThreadSnapshot:
    """Return an inbox thread older than the default reply threshold."""
    return InboxThreadSnapshot(
        thread_id="thread",
        latest_message_id="message",
        latest_from_user=False,
        user_is_recipient=True,
        latest_message_at=now - timedelta(hours=49),
    )
