"""Watcher daemon CLI composition over paths, config, and delivery."""

from __future__ import annotations

import os
import signal
import sqlite3
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict

from proactive_mcp.clock import UtcClock
from proactive_mcp.config import ConfigError, load_config
from proactive_mcp.delivery.daemon import (
    DaemonDependencies,
    DaemonFailureError,
    DaemonFailureKind,
    DaemonSchedule,
    WatcherDaemon,
    run_daemon_phase,
)
from proactive_mcp.delivery.evaluation import (
    EvaluationDependencies,
    EvaluationService,
    PreparedSources,
    SkippedSources,
)
from proactive_mcp.delivery.fallback import FallbackDispatcher
from proactive_mcp.delivery.notify import (
    NotificationHost,
    SubprocessNotificationRunner,
    parse_notification_platform,
)
from proactive_mcp.paths import resolve_paths
from proactive_mcp.scheduler import EventScheduler
from proactive_mcp.server.status import DaemonDiagnosticResponse
from proactive_mcp.situations import SituationRuntime
from proactive_mcp.sources.lazy_sync import ScheduledSourceProvider, open_source_access
from proactive_mcp.store import Store, UnsafeDatabasePathError

if TYPE_CHECKING:
    from types import FrameType

    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery.daemon import DaemonPass
    from proactive_mcp.delivery.evaluation import SourceOutcome, SourceProvider
    from proactive_mcp.scheduler import Scheduler

__all__ = [
    "DaemonOnceResponse",
    "bind_stop_signals",
    "daemon_clock",
    "open_watcher_daemon",
    "run_daemon",
    "stopping_scheduler",
]

_MAX_POLL_MINUTES: Final = 60 * 24 * 365


class DaemonOnceResponse(BaseModel):
    """PII-free observable outcome of one watcher pass."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    created: int
    notifications: int
    gmail: str
    calendar: str
    sources: str
    warning_count: int


def daemon_clock() -> Clock:
    """Return the clock the watcher uses for evaluation and heartbeats."""
    return UtcClock()


def bind_stop_signals(scheduler: EventScheduler) -> None:
    """Stop the scheduler when the process receives SIGINT or SIGTERM."""

    def _stop(_signum: int, _frame: FrameType | None) -> None:
        scheduler.stop()

    _ = signal.signal(signal.SIGINT, _stop)
    _ = signal.signal(signal.SIGTERM, _stop)


def stopping_scheduler() -> Scheduler:
    """Return a scheduler that ends the loop on Ctrl+C or SIGTERM."""
    scheduler = EventScheduler()
    bind_stop_signals(scheduler)
    return scheduler


@dataclass(frozen=True, slots=True)
class _DaemonSourceProvider:
    """Attach source-sync phase identity to the shared source provider."""

    provider: SourceProvider

    def prepare_sources(self) -> SourceOutcome:
        """Prepare sources or raise one redacted daemon failure."""
        return run_daemon_phase(
            DaemonFailureKind.SOURCE_SYNC_FAILED,
            self.provider.prepare_sources,
        )


def open_watcher_daemon(store: Store, clock: Clock) -> WatcherDaemon:
    """Compose the library watcher from local paths, store, and config."""
    paths = resolve_paths(os.environ)
    runtime = SituationRuntime.from_config(store, clock, paths.config)
    return WatcherDaemon(
        DaemonDependencies(
            pid=os.getpid(),
            clock=clock,
            heartbeat=store.daemon,
            evaluation=EvaluationService(
                EvaluationDependencies(
                    evaluator=runtime.engine,
                    sources=_DaemonSourceProvider(
                        ScheduledSourceProvider(
                            open_source_access(paths, store, clock)
                        )
                    ),
                )
            ),
            notifier=FallbackDispatcher(
                store.fallbacks,
                NotificationHost(
                    parse_notification_platform(sys.platform),
                    SubprocessNotificationRunner(),
                ),
                runtime.config.fallback,
            ),
        )
    )


def run_daemon(*, once: bool, poll_interval_minutes: float | None) -> int:
    """Run the watcher once, or until SIGINT/SIGTERM."""
    paths = resolve_paths(os.environ)
    clock = daemon_clock()
    try:
        override = _poll_override(poll_interval_minutes)
        config = load_config(paths.config)
        interval = config.daemon.poll_interval if override is None else override
        with Store(paths.database, clock=clock) as store:
            daemon = open_watcher_daemon(store, clock)
            # Persist the effective CLI/config cadence before the library start
            # so a later same-owner claim keeps this interval on the liveness row.
            run_daemon_phase(
                DaemonFailureKind.HEARTBEAT_FAILED,
                lambda: store.daemon.record_start(
                    os.getpid(),
                    poll_interval=interval,
                ),
            )
            if once:
                _emit_once(daemon.run_once())
                return 0
            _ = daemon.run_forever(DaemonSchedule(stopping_scheduler(), interval))
    except ConfigError:
        return _emit_failure(DaemonFailureKind.CONFIG_INVALID)
    except UnsafeDatabasePathError:
        return _emit_failure(DaemonFailureKind.DATABASE_UNSAFE_PATH)
    except (OSError, sqlite3.Error):
        return _emit_failure(DaemonFailureKind.DATABASE_OPEN_FAILED)
    except DaemonFailureError as failure:
        return _emit_diagnostic(failure)
    return 0


def _emit_failure(kind: DaemonFailureKind) -> int:
    return _emit_diagnostic(DaemonFailureError(kind))


def _emit_diagnostic(failure: DaemonFailureError) -> int:
    payload = DaemonDiagnosticResponse(phase=failure.phase, code=failure.code)
    _ = sys.stderr.write(f"{payload.model_dump_json()}\n")
    return 2


def _poll_override(value: float | None) -> timedelta | None:
    if value is None:
        return None
    if not 0 < value <= _MAX_POLL_MINUTES:
        raise ConfigError(
            field="poll_interval_minutes",
            reason="must be a positive number of minutes",
        )
    return timedelta(minutes=value)


def _emit_once(completed: DaemonPass) -> None:
    evaluation = completed.evaluation
    result = evaluation.result
    payload = DaemonOnceResponse(
        created=result.created,
        notifications=len(completed.notifications),
        gmail=result.gmail_freshness.status,
        calendar=result.calendar_freshness.status,
        sources=_source_token(evaluation.sources),
        warning_count=len(evaluation.warnings),
    )
    _ = sys.stdout.write(f"{payload.model_dump_json()}\n")


def _source_token(outcome: SourceOutcome) -> str:
    match outcome:  # noqa: MATCH_OK - pyright proves the union exhaustive.
        case PreparedSources():
            return "prepared"
        case SkippedSources(reason=reason):
            return reason
