"""Gate the remote source reads of one evaluation pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, Self

from proactive_mcp.delivery.evaluation import PreparedSources, SkippedSources
from proactive_mcp.sources import GoogleReadServiceFactory
from proactive_mcp.sources.credentials import CredentialStore
from proactive_mcp.store import DEFAULT_STALE_AFTER, evaluate_source_freshness

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery.evaluation import SourceOutcome, SourceSkipReason
    from proactive_mcp.paths import ProactivePaths
    from proactive_mcp.situations.inputs import EngineInputs
    from proactive_mcp.sources.credentials import GoogleCredential
    from proactive_mcp.store import (
        DaemonLiveness,
        DaemonStatus,
        LazySyncLease,
        SourceSyncState,
        Store,
    )

__all__ = [
    "CredentialLoader",
    "DaemonLivenessReader",
    "LazySourceProvider",
    "LazySyncPolicy",
    "ScheduledSourceProvider",
    "SourceAccess",
    "SourceReaderFactory",
    "SourceStateReader",
    "SourceSyncReader",
    "open_source_access",
]

_SourceStates = tuple["SourceSyncState", "SourceSyncState"]
_DAEMON_STALE_POLLS: Final = 3


class SourceSyncReader(Protocol):
    """Read ordered source snapshots for one evaluation pass."""

    def prepare_evaluation(self) -> EngineInputs:
        """Return the snapshots this pass may apply as source truth."""
        ...


class SourceReaderFactory(Protocol):
    """Build an authenticated source reader for one loaded credential."""

    def open(self, credential: GoogleCredential) -> SourceSyncReader:
        """Return a reader authorized by the given credential."""
        ...


class CredentialLoader(Protocol):
    """Load the shared read-only Google credential, if one is stored."""

    def load(self) -> GoogleCredential | None:
        """Return the stored credential, or None when none is usable."""
        ...


class SourceStateReader(Protocol):
    """Read persisted source state and reserve one in-flight lazy read."""

    def list_source_sync(self) -> _SourceStates:
        """Return Gmail and Calendar sync state in a stable order."""
        ...

    def acquire_lazy_sync_lease(
        self, *, lease_duration: timedelta
    ) -> LazySyncLease | None:
        """Atomically reserve one degraded remote read until release or expiry."""
        ...

    def release_lazy_sync_lease(self, lease: LazySyncLease) -> bool:
        """Release a degraded-read reservation if this lease still owns it."""
        ...


class DaemonLivenessReader(Protocol):
    """Report whether a watcher daemon is currently keeping sources fresh."""

    def status(self, *, stale_after: timedelta) -> DaemonStatus:
        """Return daemon liveness for one heartbeat staleness threshold."""
        ...


@dataclass(frozen=True, slots=True)
class SourceAccess:
    """The state, credential, and reader construction one read needs."""

    sync_state: SourceStateReader
    credentials: CredentialLoader
    readers: SourceReaderFactory


@dataclass(frozen=True, slots=True)
class LazySyncPolicy:
    """When a tool-time read may stand in for the watcher daemon (§4.1).

    ``min_attempt_interval`` still throttles retries after a recorded attempt.
    Concurrent first attempts take an atomic store lease of the same duration
    before credentials or the remote reader are opened.
    """

    daemon_stale_after: timedelta
    min_attempt_interval: timedelta
    stale_after: timedelta = DEFAULT_STALE_AFTER

    @classmethod
    def for_poll_interval(cls, poll_interval: timedelta) -> Self:
        """Derive degraded-mode thresholds from the daemon poll cadence."""
        return cls(
            daemon_stale_after=_DAEMON_STALE_POLLS * poll_interval,
            min_attempt_interval=poll_interval,
        )


@dataclass(frozen=True, slots=True)
class ScheduledSourceProvider:
    """Read both Google sources on the watcher's own polling cadence."""

    access: SourceAccess

    def prepare_sources(self) -> SourceOutcome:
        """Read unless authorization or credentials forbid it."""
        blocked = _authorization_skip(self.access.sync_state.list_source_sync())
        if blocked is not None:
            return SkippedSources(blocked)
        return _read(self.access)


@dataclass(frozen=True, slots=True)
class LazySourceProvider:
    """Read Google sources at tool time only while no daemon is watching."""

    access: SourceAccess
    liveness: DaemonLivenessReader
    clock: Clock
    policy: LazySyncPolicy

    def prepare_sources(self) -> SourceOutcome:
        """Read only when authorized, unwatched, stale, and uncontended."""
        states = self.access.sync_state.list_source_sync()
        blocked = _authorization_skip(states)
        if blocked is None:
            blocked = self._degraded_skip(states)
        if blocked is not None:
            return SkippedSources(blocked)
        return _read_with_lease(
            self.access,
            lease_duration=self.policy.min_attempt_interval,
        )

    def _degraded_skip(self, states: _SourceStates) -> SourceSkipReason | None:
        if _daemon_owns_reads(
            self.liveness.status(stale_after=self.policy.daemon_stale_after).liveness
        ):
            return "daemon_running"
        now = self.clock.now()
        if self._all_fresh(states, now):
            return "already_fresh"
        age = _latest_attempt_age(states, now)
        if age is not None and age < self.policy.min_attempt_interval:
            return "sync_in_flight"
        return None

    def _all_fresh(self, states: _SourceStates, now: datetime) -> bool:
        return all(
            evaluate_source_freshness(
                state,
                now,
                stale_after=self.policy.stale_after,
            ).status
            == "ok"
            for state in states
        )


def open_source_access(
    paths: ProactivePaths,
    store: Store,
    clock: Clock,
) -> SourceAccess:
    """Bind source state, credential storage, and authenticated readers."""
    credentials = CredentialStore(paths.state_directory)
    return SourceAccess(
        sync_state=store,
        credentials=credentials,
        readers=GoogleReadServiceFactory(
            store=store,
            clock=clock,
            credentials=credentials,
        ),
    )


def _read_with_lease(
    access: SourceAccess, *, lease_duration: timedelta
) -> SourceOutcome:
    """Read only after winning the singleton lease; always release it."""
    lease = access.sync_state.acquire_lazy_sync_lease(lease_duration=lease_duration)
    if lease is None:
        return SkippedSources("sync_in_flight")
    try:
        return _read(access)
    finally:
        _ = access.sync_state.release_lazy_sync_lease(lease)


def _read(access: SourceAccess) -> SourceOutcome:
    """Read both sources with the stored credential, if one exists."""
    credential = access.credentials.load()
    if credential is None:
        return SkippedSources("missing_credentials")
    return PreparedSources(access.readers.open(credential).prepare_evaluation())


def _authorization_skip(states: _SourceStates) -> SourceSkipReason | None:
    """Return why the shared Google grant forbids a read, if it does."""
    for state in states:
        # Exhaustive over SourceAuthState: a new state leaves ``blocked``
        # unbound, breaking this match at type-check time rather than silently
        # attempting an unauthorized read.
        match state.auth_state:
            case "not_configured":
                blocked: SourceSkipReason | None = "not_configured"
            case "needs_reauth":
                blocked = "needs_reauth"
            case "configured":
                blocked = None
        if blocked is not None:
            return blocked
    return None


def _daemon_owns_reads(liveness: DaemonLiveness) -> bool:
    """Return whether a live daemon is already keeping both sources fresh."""
    # Exhaustive over DaemonLiveness: a new value leaves ``watching`` unbound,
    # breaking this match at type-check time rather than silently duplicating
    # the daemon's reads.
    match liveness:
        case "running":
            watching = True
        case "never_started" | "stale" | "stopped":
            watching = False
    return watching


def _latest_attempt_age(states: _SourceStates, now: datetime) -> timedelta | None:
    """Return how long ago any instance last attempted a source read."""
    attempts = [
        state.last_attempt_at for state in states if state.last_attempt_at is not None
    ]
    return None if not attempts else now - max(attempts)
