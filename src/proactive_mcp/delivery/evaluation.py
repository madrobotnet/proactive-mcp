"""One shared Situation evaluation pass for the daemon and proactive_check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from proactive_mcp.situations.inputs import EngineInputs

if TYPE_CHECKING:
    from proactive_mcp.situations.engine import EvaluationResult

__all__ = [
    "EvaluationDependencies",
    "EvaluationPass",
    "EvaluationService",
    "PreparedSources",
    "SituationEvaluator",
    "SkippedSources",
    "SourceOutcome",
    "SourceProvider",
    "SourceSkipReason",
]

SourceSkipReason: TypeAlias = Literal[
    "not_configured",
    "needs_reauth",
    "missing_credentials",
    "credential_storage_unavailable",
    "daemon_running",
    "already_fresh",
    "sync_in_flight",
]


@dataclass(frozen=True, slots=True)
class PreparedSources:
    """The remote source snapshots one pass may apply as source truth."""

    inputs: EngineInputs


@dataclass(frozen=True, slots=True)
class SkippedSources:
    """The named reason one pass performed no remote source read."""

    reason: SourceSkipReason


SourceOutcome: TypeAlias = PreparedSources | SkippedSources


class SourceProvider(Protocol):
    """Decide and perform the remote source reads of one pass."""

    def prepare_sources(self) -> SourceOutcome:
        """Return prepared snapshots, or the reason none were read."""
        ...


class SituationEvaluator(Protocol):
    """Evaluate detectors and stored situation truth for one pass."""

    def evaluate(self, inputs: EngineInputs) -> EvaluationResult:
        """Run one deterministic evaluation over the given snapshots."""
        ...


@dataclass(frozen=True, slots=True)
class EvaluationDependencies:
    """The evaluator and source provider of one evaluation service."""

    evaluator: SituationEvaluator
    sources: SourceProvider


@dataclass(frozen=True, slots=True)
class EvaluationPass:
    """The observable outcome of one shared evaluation pass.

    ``warnings`` always names every skipped or unhealthy source, so an
    empty situation list can never be presented as an all-clear (§7).
    """

    result: EvaluationResult
    sources: SourceOutcome
    warnings: tuple[str, ...]


class EvaluationService:
    """Run the evaluation pass the daemon and proactive_check both use.

    Skipping remote reads never skips the pass: local memories are always
    evaluated, and stored situations of an unread source are kept rather
    than resolved.
    """

    _dependencies: EvaluationDependencies

    def __init__(self, dependencies: EvaluationDependencies) -> None:
        """Bind one evaluator and one source provider to this service."""
        self._dependencies = dependencies

    def run_once(self) -> EvaluationPass:
        """Read what the source gate permits, then evaluate exactly once."""
        outcome = self._dependencies.sources.prepare_sources()
        match outcome:
            case PreparedSources(inputs=inputs):
                skipped: tuple[str, ...] = ()
            case SkippedSources(reason=reason):
                inputs = EngineInputs()
                skipped = _skip_warnings(reason)
        result = self._dependencies.evaluator.evaluate(inputs)
        return EvaluationPass(
            result=result,
            sources=outcome,
            warnings=(*skipped, *result.warnings),
        )


def _skip_warnings(reason: SourceSkipReason) -> tuple[str, ...]:
    """Return the operator action a skipped remote read requires, if any."""
    # Exhaustive over SourceSkipReason: a new reason leaves ``guidance``
    # unbound, breaking this match at type-check time rather than silently
    # losing that reason's operator guidance.
    match reason:
        case "not_configured":
            guidance = "google: sources are not configured; run proactive-mcp setup"
        case "needs_reauth":
            guidance = (
                "google: authorization is no longer valid; "
                "run proactive-mcp setup --reauth"
            )
        case "missing_credentials":
            guidance = "google: stored credentials are missing; run proactive-mcp setup"
        case "credential_storage_unavailable":
            guidance = (
                "google: credential storage is unavailable; "
                "unlock this session's OS keyring, then run "
                "proactive-mcp setup if reads stay skipped"
            )
        case "sync_in_flight":
            guidance = "google: another read attempt is in flight; read skipped"
        case "daemon_running" | "already_fresh":
            guidance = ""
    return () if guidance == "" else (guidance,)
