"""MCP tool implementations for the M4 situation delivery surface."""

from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from proactive_mcp.clock import UtcClock
from proactive_mcp.delivery import EvaluationDependencies, EvaluationService
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server.situation_requests import MuteScope, parse_snooze_until
from proactive_mcp.server.situation_responses import (
    ListSituationsResponse,
    MuteResponse,
    ProactiveCheckResponse,
    SituationResponse,
    budget_response,
    google_freshness_response,
    situation_response,
)
from proactive_mcp.situations import SituationRuntime
from proactive_mcp.sources.lazy_sync import (
    LazySourceProvider,
    LazySyncPolicy,
    open_source_access,
)
from proactive_mcp.store import SituationNotFoundError, SituationState, Store

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery import EvaluationRunner
    from proactive_mcp.paths import ProactivePaths
    from proactive_mcp.store import SituationStore

__all__ = [
    "SituationToolDependencies",
    "SituationToolService",
    "acknowledge_situation",
    "get_situation",
    "list_situations",
    "mute_situation",
    "open_situation_service",
    "proactive_check",
    "snooze_situation",
]

_SituationId = Annotated[int, Field(validation_alias="id")]


@dataclass(frozen=True, slots=True)
class SituationToolDependencies:
    """The store, clock, policy runtime, and evaluation one service uses."""

    store: Store
    clock: Clock
    runtime: SituationRuntime
    evaluation: EvaluationRunner


class SituationToolService:
    """Answer the six delivery tools from one open store.

    ``proactive_check`` runs the shared evaluation pass first and then
    claims rows atomically, so several agent sessions may check the same
    database without either of them delivering one situation twice (§5.1).
    """

    _dependencies: SituationToolDependencies

    def __init__(self, dependencies: SituationToolDependencies) -> None:
        """Bind this service to one open store and its collaborators."""
        self._dependencies = dependencies

    def proactive_check(self) -> ProactiveCheckResponse:
        """Evaluate, claim what attention policy allows, report what is held."""
        completed = self._dependencies.evaluation.run_once()
        now = self._dependencies.clock.now()
        attention = self._dependencies.runtime.attention
        claimed = attention.claim_for_delivery(now)
        held_count = len(self._situations.list_situations(state="pending"))
        return ProactiveCheckResponse(
            situations=tuple(situation_response(item) for item in claimed),
            freshness=google_freshness_response(
                completed.result.gmail_freshness,
                completed.result.calendar_freshness,
            ),
            budget=budget_response(attention.budget_usage(now)),
            held_count=held_count,
            warnings=completed.warnings,
            all_clear=not claimed and held_count == 0 and not completed.warnings,
        )

    def list_situations(
        self,
        state: SituationState | None = None,
    ) -> ListSituationsResponse:
        """List stored situations, optionally filtered to one state."""
        return ListSituationsResponse(
            items=tuple(
                situation_response(item)
                for item in self._situations.list_situations(state)
            )
        )

    def get_situation(self, situation_id: int) -> SituationResponse:
        """Return one situation with its isolated evidence."""
        situation = self._situations.get_situation(situation_id)
        if situation is None:
            raise SituationNotFoundError(situation_id)
        return situation_response(situation)

    def acknowledge_situation(self, situation_id: int) -> SituationResponse:
        """Record that the user handled one delivered situation."""
        return situation_response(self._situations.acknowledge_situation(situation_id))

    def snooze_situation(self, situation_id: int, until: str) -> SituationResponse:
        """Hold one delivered situation until an aware future instant."""
        wake = parse_snooze_until(until, self._dependencies.clock.now())
        return situation_response(self._situations.snooze_situation(situation_id, wake))

    def mute_situation(
        self,
        situation_id: int,
        scope: MuteScope = "instance",
    ) -> MuteResponse:
        """Mute one delivered situation, or its whole type in one transaction."""
        situations = self._situations
        # Exhaustive over MuteScope: a new scope leaves ``muted`` unbound,
        # breaking this match at type-check time rather than silently muting
        # the instance only.
        match scope:
            case "instance":
                muted = situations.mute_situation(situation_id)
            case "type":
                muted = situations.mute_situation_type(situation_id)
        return MuteResponse(
            situation=situation_response(muted),
            scope=scope,
            muted_types=situations.muted_situation_types(),
        )

    @property
    def _situations(self) -> SituationStore:
        return self._dependencies.store.situations


def open_situation_service(
    store: Store,
    clock: Clock,
    paths: ProactivePaths,
) -> SituationToolService:
    """Wire one degraded-mode tool service for an already open store (§4.1)."""
    runtime = SituationRuntime.from_config(store, clock, paths.config)
    poll_interval = (
        store.daemon.status().poll_interval or runtime.config.daemon.poll_interval
    )
    return SituationToolService(
        SituationToolDependencies(
            store=store,
            clock=clock,
            runtime=runtime,
            evaluation=EvaluationService(
                EvaluationDependencies(
                    evaluator=runtime.engine,
                    sources=LazySourceProvider(
                        access=open_source_access(paths, store, clock),
                        liveness=store.daemon,
                        clock=clock,
                        policy=LazySyncPolicy.for_poll_interval(poll_interval),
                    ),
                )
            ),
        )
    )


class _ToolCall:
    """The store connection and service one tool call owns.

    Deliberately not a ``@contextmanager``: contextlib assigns
    ``__traceback__`` while unwinding, which the store's frozen slots
    exceptions reject, so a failing tool would report that TypeError
    instead of the transition or lookup that actually failed.
    """

    _resources: ExitStack

    def __init__(self) -> None:
        """Prepare the resource stack this call closes on exit."""
        self._resources = ExitStack()

    def __enter__(self) -> SituationToolService:
        """Open this installation's store and wire its tool service."""
        paths = resolve_paths(os.environ)
        clock = UtcClock()
        store = self._resources.enter_context(Store(paths.database, clock=clock))
        return open_situation_service(store, clock, paths)

    def __exit__(self, *details: object) -> None:
        """Close the store connection this call opened."""
        self._resources.close()


async def proactive_check() -> str:
    """Return the situations worth raising now and mark them delivered."""
    with _ToolCall() as service:
        return service.proactive_check().model_dump_json()


async def list_situations(*, state: SituationState | None = None) -> str:
    """Return stored situations without delivering any of them."""
    with _ToolCall() as service:
        return service.list_situations(state).model_dump_json()


async def get_situation(situation_id: _SituationId) -> str:
    """Return one situation and its evidence by id."""
    with _ToolCall() as service:
        return service.get_situation(situation_id).model_dump_json()


async def acknowledge_situation(situation_id: _SituationId) -> str:
    """Mark one delivered situation as handled by the user."""
    with _ToolCall() as service:
        return service.acknowledge_situation(situation_id).model_dump_json()


async def snooze_situation(situation_id: _SituationId, until: str) -> str:
    """Hold one delivered situation until the given wake time."""
    with _ToolCall() as service:
        return service.snooze_situation(situation_id, until).model_dump_json()


async def mute_situation(
    situation_id: _SituationId,
    *,
    scope: MuteScope = "instance",
) -> str:
    """Mute one delivered situation, or every situation of its type."""
    with _ToolCall() as service:
        return service.mute_situation(situation_id, scope).model_dump_json()
