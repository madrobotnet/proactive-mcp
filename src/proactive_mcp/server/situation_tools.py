"""MCP tool implementations for the M4 situation delivery surface."""

from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from proactive_mcp.clock import UtcClock
from proactive_mcp.delivery import (
    EvaluationDependencies,
    EvaluationService,
    SkippedSources,
)
from proactive_mcp.paths import resolve_paths
from proactive_mcp.server.situation_requests import MuteScope, parse_snooze_until
from proactive_mcp.server.situation_responses import (
    ConfirmDeliveryResponse,
    ListSituationsResponse,
    MuteResponse,
    ProactiveCheckResponse,
    SituationResponse,
    SourceAuthorizationState,
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
from proactive_mcp.store import (
    CollectorProfile,
    DeliveryReceiptError,
    SituationNotFoundError,
    SituationState,
    Store,
    evaluate_source_freshness,
)
from proactive_mcp.store.situations import MAX_SITUATION_PAGE_SIZE

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock
    from proactive_mcp.delivery import EvaluationRunner
    from proactive_mcp.paths import ProactivePaths
    from proactive_mcp.store import Situation, SituationStore

__all__ = [
    "SituationToolDependencies",
    "SituationToolService",
    "acknowledge_situation",
    "confirm_delivery",
    "get_situation",
    "list_situations",
    "mute_situation",
    "open_situation_service",
    "proactive_check",
    "proactive_check_for_profile",
    "snooze_situation",
]

_SituationId = Annotated[int, Field(validation_alias="id")]
_MIN_EVALUATION_INTERVAL = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class SituationToolDependencies:
    """The store, clock, policy runtime, and evaluation one service uses."""

    store: Store
    clock: Clock
    runtime: SituationRuntime
    evaluation: EvaluationRunner


class SituationToolService:
    """Answer the seven delivery tools from one open store.

    ``proactive_check`` runs the shared evaluation pass and leases rows
    atomically. Only ``confirm_delivery`` commits the delivery after the host
    received the result, preventing silent loss when a transport fails (§5.1).
    """

    _dependencies: SituationToolDependencies

    def __init__(self, dependencies: SituationToolDependencies) -> None:
        """Bind this service to one open store and its collaborators."""
        self._dependencies = dependencies

    def proactive_check(
        self,
        *,
        profile: CollectorProfile = "full",
    ) -> ProactiveCheckResponse:
        """Evaluate, lease what attention policy allows, report what is held."""
        now = self._dependencies.clock.now()
        evaluation_warnings: tuple[str, ...] = ()
        if self._dependencies.store.try_start_evaluation(
            minimum_interval=_MIN_EVALUATION_INTERVAL
        ):
            completed = self._dependencies.evaluation.run_once()
            evaluation_warnings = tuple(
                warning
                for warning in completed.warnings
                if not warning.startswith(("gmail:", "calendar:"))
            )
            if completed.sources == SkippedSources("credential_storage_unavailable"):
                self._dependencies.store.record_credential_state("unavailable")
            elif completed.sources == SkippedSources("missing_credentials"):
                self._dependencies.store.record_credential_state("missing")
        attention = self._dependencies.runtime.attention
        reservation = attention.reserve_for_delivery(now)
        claimed = reservation.situations
        held_count = max(0, self._situations.count_situations("pending") - len(claimed))
        receipt_token = reservation.claim_token if claimed else None
        source_health = self._dependencies.store.source_health_snapshot()
        gmail_freshness = evaluate_source_freshness(source_health.gmail, now)
        calendar_freshness = evaluate_source_freshness(source_health.calendar, now)
        source_warnings = tuple(
            f"{source}: source is {freshness.status}"
            for source, freshness in (
                ("gmail", gmail_freshness),
                ("calendar", calendar_freshness),
            )
            if freshness.status != "ok"
        )
        warnings = tuple(dict.fromkeys((*evaluation_warnings, *source_warnings)))
        authorization_override: SourceAuthorizationState | None = (
            "credential_unavailable"
            if source_health.credential.state == "unavailable"
            else (
                "credential_missing"
                if source_health.credential.state == "missing"
                else None
            )
        )
        response = ProactiveCheckResponse(
            requires_confirmation=bool(claimed) and receipt_token is not None,
            situations=tuple(
                situation_response(
                    item,
                    lease_expires_at=reservation.expires_at,
                )
                for item in claimed
            ),
            receipt_token=receipt_token,
            freshness=google_freshness_response(
                gmail_freshness,
                calendar_freshness,
                source_health.gmail_diagnostics,
                gmail_state=source_health.gmail,
                calendar_state=source_health.calendar,
                gmail_generation=source_health.gmail_generation,
                calendar_generation=source_health.calendar_generation,
                now=now,
                authorization_override=authorization_override,
            ),
            budget=budget_response(attention.budget_usage(now)),
            held_count=held_count,
            warnings=warnings,
            all_clear=not claimed and held_count == 0 and not warnings,
        )
        self._dependencies.store.collectors.record_check(profile)
        return response

    def list_situations(
        self,
        state: SituationState | None = None,
        *,
        after_id: int = 0,
        limit: int = 20,
    ) -> ListSituationsResponse:
        """List stored situations, optionally filtered to one state."""
        page = self._situations.list_situations(
            state,
            after_id=after_id,
            limit=limit,
        )
        return ListSituationsResponse(
            items=tuple(self._situation_response(item) for item in page),
            next_after_id=page[-1].id if len(page) == limit and page else None,
        )

    def get_situation(self, situation_id: int) -> SituationResponse:
        """Return one situation with its isolated evidence."""
        situation = self._situations.get_situation(situation_id)
        if situation is None:
            raise SituationNotFoundError(situation_id)
        return self._situation_response(situation)

    def confirm_delivery(
        self,
        receipt_token: str,
        *,
        profile: CollectorProfile = "full",
    ) -> ConfirmDeliveryResponse:
        """Confirm that the MCP host received a proactive-check result."""
        try:
            confirmation = self._situations.confirm_delivery(receipt_token)
        except DeliveryReceiptError:
            return ConfirmDeliveryResponse(
                status="invalid_or_expired",
                delivered_count=0,
            )
        self._dependencies.store.collectors.record_confirm(profile)
        return ConfirmDeliveryResponse(
            status=confirmation.status,
            delivered_count=confirmation.delivered_count,
        )

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

    def _situation_response(self, situation: Situation) -> SituationResponse:
        lease_expires_at = self._situations.active_lease_expires_at(
            situation.id,
            self._dependencies.clock.now(),
        )
        return situation_response(situation, lease_expires_at=lease_expires_at)


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
    """Return situations under a lease that requires receipt confirmation."""
    return await proactive_check_for_profile("full")


async def proactive_check_for_profile(profile: CollectorProfile) -> str:
    """Return one profile's leased situations and record its observation."""
    with _ToolCall() as service:
        return service.proactive_check(profile=profile).model_dump_json()


async def list_situations(
    *,
    state: SituationState | None = None,
    after_id: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=MAX_SITUATION_PAGE_SIZE)] = 20,
) -> str:
    """Return stored situations without delivering any of them."""
    with _ToolCall() as service:
        return service.list_situations(
            state,
            after_id=after_id,
            limit=limit,
        ).model_dump_json()


async def get_situation(situation_id: _SituationId) -> str:
    """Return one situation and its evidence by id."""
    with _ToolCall() as service:
        return service.get_situation(situation_id).model_dump_json()


async def confirm_delivery(receipt_token: str) -> str:
    """Confirm receipt of a proactive result before it becomes delivered."""
    return await confirm_delivery_for_profile(receipt_token, "full")


async def confirm_delivery_for_profile(
    receipt_token: str,
    profile: CollectorProfile,
) -> str:
    """Confirm one profile's receipt and record a successful observation."""
    with _ToolCall() as service:
        return service.confirm_delivery(
            receipt_token,
            profile=profile,
        ).model_dump_json()


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
