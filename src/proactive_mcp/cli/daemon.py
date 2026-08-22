"""Watcher daemon CLI composition over paths, config, and delivery."""

from __future__ import annotations

import os
import signal
import sqlite3
import sys
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict

from proactive_mcp.clock import UtcClock
from proactive_mcp.config import ConfigError, load_config
from proactive_mcp.delivery.daemon import (
    DaemonDependencies,
    DaemonSchedule,
    WatcherDaemon,
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
from proactive_mcp.situations import SituationRuntime
from proactive_mcp.sources.lazy_sync import ScheduledSourceProvider, open_source_access
from proactive_mcp.store import Store, UnsafeDatabasePathError

if TYPE_CHECKING:
    from types import FrameType

    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery.daemon import DaemonPass
    from proactive_mcp.delivery.evaluation import SourceOutcome
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
                    sources=ScheduledSourceProvider(
                        open_source_access(paths, store, clock)
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
            store.daemon.record_start(os.getpid(), poll_interval=interval)
            if once:
                _emit_once(daemon.run_once())
                return 0
            _ = daemon.run_forever(DaemonSchedule(stopping_scheduler(), interval))
    except (ConfigError, UnsafeDatabasePathError) as error:
        _ = sys.stderr.write(f"error: {error}\n")
        return 2
    except (OSError, sqlite3.Error):
        _ = sys.stderr.write("error: daemon infrastructure failure\n")
        return 1
    return 0


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
    match outcome:
        case PreparedSources():
            return "prepared"
        case SkippedSources(reason=reason):
            return reason
