"""Watcher daemon CLI composition over paths, config, and delivery."""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict

from proactive_mcp.cli.process_liveness import process_is_alive
from proactive_mcp.cli.service_task_scheduler_ready import signal_task_scheduler_ready
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
from proactive_mcp.server.situation_responses import (
    SourceReadDiagnosticsResponse,
    gmail_freshness_diagnostics,
    source_read_diagnostics_response,
)
from proactive_mcp.server.status import DaemonDiagnosticResponse
from proactive_mcp.situations import SituationRuntime
from proactive_mcp.sources.lazy_sync import ScheduledSourceProvider, open_source_access
from proactive_mcp.store import (
    ReceiptErasurePendingError,
    Store,
    UnsafeDatabasePathError,
)

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
    "notify_service_ready",
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
    gmail_diagnostics: SourceReadDiagnosticsResponse
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


def notify_service_ready() -> None:
    """Notify systemd after startup ownership is durably recorded."""
    signal_task_scheduler_ready(resolve_paths(os.environ).database)
    configured = os.environ.get("NOTIFY_SOCKET")
    if configured is None:
        return
    address = f"\0{configured[1:]}" if configured.startswith("@") else configured
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.connect(address)
        notifier.sendall(b"READY=1")


def _process_is_alive(pid: int) -> bool:
    return process_is_alive(pid)


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
                        ScheduledSourceProvider(open_source_access(paths, store, clock))
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
    result = 0
    try:
        override = _poll_override(poll_interval_minutes)
        config = load_config(paths.config)
        interval = config.daemon.poll_interval if override is None else override
        with Store(paths.database, clock=clock) as store:
            daemon = open_watcher_daemon(store, clock)
            # Persist cadence before the library's idempotent same-owner claim.
            claimed = run_daemon_phase(
                DaemonFailureKind.HEARTBEAT_FAILED,
                lambda: store.daemon.try_record_start(
                    os.getpid(),
                    poll_interval=interval,
                    incumbent_is_alive=_process_is_alive,
                ),
            )
            if not claimed:
                result = _emit_failure(DaemonFailureKind.OWNERSHIP_CONFLICT)
            elif once:
                _emit_once(daemon.run_once())
            else:
                try:
                    run_daemon_phase(
                        DaemonFailureKind.SERVICE_NOTIFY_FAILED,
                        notify_service_ready,
                    )
                except DaemonFailureError as failure:
                    failure_phase = failure.phase
                    failure_code = failure.code
                    try:
                        run_daemon_phase(
                            DaemonFailureKind.HEARTBEAT_FAILED,
                            lambda: store.daemon.record_run_started("continuous"),
                        )
                        run_daemon_phase(
                            DaemonFailureKind.HEARTBEAT_FAILED,
                            lambda: store.daemon.record_run_outcome(
                                "failed",
                                failure_phase=failure_phase,
                                failure_code=failure_code,
                            ),
                        )
                    finally:
                        run_daemon_phase(
                            DaemonFailureKind.HEARTBEAT_FAILED,
                            store.daemon.record_stop,
                        )
                    raise
                _ = daemon.run_forever(DaemonSchedule(stopping_scheduler(), interval))
    except ConfigError:
        return _emit_failure(DaemonFailureKind.CONFIG_INVALID)
    except UnsafeDatabasePathError:
        return _emit_failure(DaemonFailureKind.DATABASE_UNSAFE_PATH)
    except ReceiptErasurePendingError:
        return _emit_failure(DaemonFailureKind.DATABASE_OPEN_FAILED)
    except (OSError, sqlite3.Error):
        return _emit_failure(DaemonFailureKind.DATABASE_OPEN_FAILED)
    except DaemonFailureError as failure:
        return _emit_diagnostic(failure)
    return result


def _emit_failure(kind: DaemonFailureKind) -> int:
    return _emit_diagnostic(DaemonFailureError(kind))


def _emit_diagnostic(failure: DaemonFailureError) -> int:
    payload = DaemonDiagnosticResponse(phase=failure.phase, code=failure.code)
    _ = sys.stderr.write(f"{payload.model_dump_json()}\n")
    return _failure_exit_status(failure.kind)


def _failure_exit_status(kind: DaemonFailureKind) -> int:
    match kind:
        case (
            DaemonFailureKind.CONFIG_INVALID
            | DaemonFailureKind.DATABASE_UNSAFE_PATH
            | DaemonFailureKind.CREDENTIAL_UNAVAILABLE
            | DaemonFailureKind.OWNERSHIP_CONFLICT
        ):
            return 2
        case (
            DaemonFailureKind.DATABASE_OPEN_FAILED
            | DaemonFailureKind.SOURCE_SYNC_FAILED
            | DaemonFailureKind.EVALUATION_FAILED
            | DaemonFailureKind.NOTIFICATION_FAILED
            | DaemonFailureKind.HEARTBEAT_FAILED
            | DaemonFailureKind.SERVICE_NOTIFY_FAILED
        ):
            return 1


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
    diagnostics = result.accepted_gmail_diagnostics
    if diagnostics is None:
        diagnostics = gmail_freshness_diagnostics(result.gmail_freshness)
    payload = DaemonOnceResponse(
        created=result.created,
        notifications=len(completed.notifications),
        gmail=result.gmail_freshness.status,
        gmail_diagnostics=source_read_diagnostics_response(diagnostics),
        calendar=result.calendar_freshness.status,
        sources=_source_token(evaluation.sources),
        warning_count=len(evaluation.warnings),
    )
    _ = sys.stdout.write(f"{payload.model_dump_json()}\n")


def _source_token(outcome: SourceOutcome) -> str:
    # Exhaustive: basedpyright proves every SourceOutcome variant is handled.
    match outcome:
        case PreparedSources():
            return "prepared"
        case SkippedSources(reason=reason):
            return reason
