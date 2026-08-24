"""Status document: database, sources, daemon, fallback, budget, and deliveries."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict

from proactive_mcp.clock import UtcClock
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server.situation_responses import (
    BudgetResponse,
    GoogleFreshnessResponse,
    budget_response,
    google_freshness_response,
)
from proactive_mcp.situations import SituationRuntime
from proactive_mcp.sources.lazy_sync import LazySyncPolicy
from proactive_mcp.store import (
    DaemonLiveness,
    FallbackFailureCode,
    Store,
    evaluate_source_freshness,
)

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock
    from proactive_mcp.paths import ProactivePaths
    from proactive_mcp.store import DaemonStatus, SourceFreshnessStatus

__all__ = [
    "DaemonDiagnosticResponse",
    "DaemonStatusResponse",
    "DatabaseStatusResponse",
    "DeliveriesStatusResponse",
    "FallbackStatusResponse",
    "StatusResponse",
    "build_status",
    "status_response",
]

_SOURCE_WARNINGS: Final[dict[SourceFreshnessStatus, str]] = {
    "ok": "",
    "needs_reauth": (
        "Google {source} requires reauthentication; run proactive-mcp setup --reauth."
    ),
    "not_configured": "Google {source} is not configured; run proactive-mcp setup.",
    "never_synced": "Google {source} has not completed a read sync.",
    "stale": "Google {source} data is stale.",
    "error": "Google {source} read sync failed.",
}


class DatabaseStatusResponse(BaseModel):
    """Database details exposed by the M0 status contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["healthy"]
    path: str
    journal_mode: str
    busy_timeout: int
    migration_version: int


class DaemonDiagnosticResponse(BaseModel):
    """Journal-safe identity of one failed daemon phase."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    phase: Literal[
        "config", "database", "credential", "source_sync", "evaluation",
        "notification", "heartbeat", "runtime_ownership",
    ]
    code: Literal[
        "invalid", "unsafe_path", "open_failed", "unavailable", "failed",
        "ownership_conflict",
    ]


class DaemonStatusResponse(BaseModel):
    """Watcher liveness; without a live daemon the OS fallback cannot run.

    ``status`` is the coarse verdict a client branches on; ``liveness``
    carries the precise state behind it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["running", "not_running"]
    liveness: DaemonLiveness
    pid: int | None
    started_at: str | None
    heartbeat_at: str | None
    cycle_count: int


class FallbackStatusResponse(BaseModel):
    """One-shot OS notification outcomes as counts and redacted codes only."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    claimed: int
    sent: int
    failed: int
    failure_codes: tuple[FallbackFailureCode, ...]


class DeliveriesStatusResponse(BaseModel):
    """Cumulative immutable delivery-event count, including critical claims."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    total: int


class StatusResponse(BaseModel):
    """Typed status result shared by the CLI and the get_status tool."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    overall: Literal["ok", "degraded"]
    database: DatabaseStatusResponse
    google: GoogleFreshnessResponse
    daemon: DaemonStatusResponse
    fallback: FallbackStatusResponse
    budget: BudgetResponse
    deliveries: DeliveriesStatusResponse
    warnings: tuple[str, ...]


def build_status() -> StatusResponse:
    """Build the status document for this installation's state layout."""
    paths = resolve_paths(os.environ)
    clock = UtcClock()
    with Store(paths.database, clock=clock) as store:
        return status_response(store, clock, paths)


def status_response(
    store: Store,
    clock: Clock,
    paths: ProactivePaths,
) -> StatusResponse:
    """Build one status document from persisted state, reading no source."""
    now = clock.now()
    runtime = SituationRuntime.from_config(store, clock, paths.config)
    gmail_state, calendar_state = store.list_source_sync()
    google = google_freshness_response(
        evaluate_source_freshness(gmail_state, now),
        evaluate_source_freshness(calendar_state, now),
    )
    poll_interval = (
        store.daemon.status().poll_interval or runtime.config.daemon.poll_interval
    )
    daemon = store.daemon.status(
        stale_after=LazySyncPolicy.for_poll_interval(poll_interval).daemon_stale_after
    )
    fallback = _fallback_status(store)
    observed = store.status()
    warnings = (
        *_source_warnings(google),
        *_daemon_warnings(daemon.liveness),
        *_fallback_warnings(fallback),
    )
    return StatusResponse(
        overall="ok" if warnings == () else "degraded",
        database=DatabaseStatusResponse(
            status="healthy",
            path=str(observed.path),
            journal_mode=observed.journal_mode,
            busy_timeout=observed.busy_timeout,
            migration_version=observed.migration_version,
        ),
        google=google,
        daemon=_daemon_response(daemon),
        fallback=fallback,
        budget=budget_response(runtime.attention.budget_usage(now)),
        deliveries=DeliveriesStatusResponse(total=store.situations.count_deliveries()),
        warnings=warnings,
    )


def _daemon_response(daemon: DaemonStatus) -> DaemonStatusResponse:
    """Serialize daemon liveness without any command line or environment."""
    return DaemonStatusResponse(
        status="running" if daemon.liveness == "running" else "not_running",
        liveness=daemon.liveness,
        pid=daemon.pid,
        started_at=daemon.started_at,
        heartbeat_at=daemon.heartbeat_at,
        cycle_count=daemon.cycle_count,
    )


def _fallback_status(store: Store) -> FallbackStatusResponse:
    """Count OS notification outcomes without reading their content."""
    summary = store.fallbacks.summary()
    return FallbackStatusResponse(
        claimed=summary.claimed,
        sent=summary.sent,
        failed=summary.failed,
        failure_codes=summary.failure_codes,
    )


def _source_warnings(google: GoogleFreshnessResponse) -> tuple[str, ...]:
    """Return the operator action every source that is not fresh requires."""
    templates = (
        (_SOURCE_WARNINGS[google.gmail.status], "Gmail"),
        (_SOURCE_WARNINGS[google.calendar.status], "Calendar"),
    )
    return tuple(
        template.format(source=source)
        for template, source in templates
        if template != ""
    )


def _daemon_warnings(liveness: DaemonLiveness) -> tuple[str, ...]:
    """Name the fallback consequence of every non-running daemon state."""
    # Exhaustive over DaemonLiveness: a new value leaves ``warning`` unbound,
    # breaking this match at type-check time rather than silently reporting a
    # healthy installation.
    match liveness:
        case "running":
            warning = ""
        case "never_started":
            warning = "Daemon has never run; OS notification fallback is unavailable."
        case "stopped":
            warning = "Daemon is stopped; OS notification fallback is unavailable."
        case "stale":
            warning = "Daemon heartbeat is stale; OS notification fallback may lag."
    return () if warning == "" else (warning,)


def _fallback_warnings(fallback: FallbackStatusResponse) -> tuple[str, ...]:
    """Surface failed OS notifications instead of swallowing them (§7)."""
    if fallback.failed == 0:
        return ()
    return (f"OS notification fallback failed for {fallback.failed} situation(s).",)
