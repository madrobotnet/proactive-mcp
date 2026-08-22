"""Hermetic wiring shared by the M4 situation tool tests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from textwrap import dedent
from threading import Event
from typing import TYPE_CHECKING, ClassVar, Final

from mcp.types import TextContent
from pydantic import BaseModel, ConfigDict, Field

from proactive_mcp.delivery import EvaluationDependencies, EvaluationService
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_tools import (
    SituationToolDependencies,
    SituationToolService,
)
from proactive_mcp.situations import SituationRuntime
from proactive_mcp.store import Detection, SituationEvidence, Store
from tests.daemon_test_support import SkippedSourceProvider
from tests.situation_test_support import FakeClock

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path
    from threading import Barrier

    from mcp.types import CallToolResult, Tool

    from proactive_mcp.delivery.evaluation import SourceSkipReason
    from proactive_mcp.server.situation_responses import (
        ProactiveCheckResponse,
        SituationResponse,
    )
    from proactive_mcp.store import SituationPriority

UNTRUSTED_SUBJECT: Final = "CANARY_SUBJECT_quoted-external-text"
RACE_TIMEOUT: Final = 20.0
DATABASE_NAME: Final = "proactive.db"


class ToolProperty(BaseModel):
    """One advertised MCP input property, kept opaque on purpose."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")


class ToolSchema(BaseModel):
    """The MCP input schema fields these tests assert on."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    properties: dict[str, ToolProperty] = Field(default_factory=dict)
    required: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolHarness:
    """One temp-directory installation wired to the situation tool service."""

    paths: ProactivePaths
    store: Store
    clock: FakeClock
    dependencies: SituationToolDependencies
    service: SituationToolService


class BarrierClock:
    """Release every racing instance at the instant it reads the time.

    ``proactive_check`` reads the clock once, between its evaluation pass
    and its claim, so waiting there lands every racing instance on the
    claim together instead of letting them serialize behind evaluation.
    """

    _clock: FakeClock
    _barrier: Barrier
    _waited: Event

    def __init__(self, clock: FakeClock, barrier: Barrier) -> None:
        """Bind one instance's clock to the barrier its rivals share."""
        self._clock = clock
        self._barrier = barrier
        self._waited = Event()

    def now(self) -> datetime:
        """Return the fixed instant, waiting out the rivals on first read."""
        if not self._waited.is_set():
            self._waited.set()
            assert self._barrier.wait(timeout=RACE_TIMEOUT) >= 0
        return self._clock.now()


def write_config(state_directory: Path, *, daily_budget: int = 4) -> None:
    """Pin the timezone and budget the tool tests reason about."""
    _ = (state_directory / "config.toml").write_text(
        dedent(f"""\
            [attention]
            timezone = "UTC"
            daily_budget = {daily_budget}
            """),
        encoding="utf-8",
    )


@contextmanager
def open_harness(
    state_directory: Path,
    now: datetime,
    sources: SourceSkipReason = "missing_credentials",
) -> Generator[ToolHarness]:
    """Open one tool service whose evaluation never reads a remote source."""
    paths = ProactivePaths.for_database(state_directory / DATABASE_NAME)
    if not paths.config.exists():
        write_config(paths.state_directory)
    clock = FakeClock(now)
    with Store(paths.database, clock=clock) as store:
        runtime = SituationRuntime.from_config(store, clock, paths.config)
        dependencies = SituationToolDependencies(
            store=store,
            clock=clock,
            runtime=runtime,
            evaluation=EvaluationService(
                EvaluationDependencies(
                    evaluator=runtime.engine,
                    sources=SkippedSourceProvider(sources),
                )
            ),
        )
        yield ToolHarness(
            paths=paths,
            store=store,
            clock=clock,
            dependencies=dependencies,
            service=SituationToolService(dependencies),
        )


def pending_detection(key: str, priority: SituationPriority = "routine") -> Detection:
    """Build one deliverable detection carrying untrusted quoted evidence."""
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=key,
        priority=priority,
        title=f"Fixture conflict {key}",
        why_now="Fixture delivery candidate",
        evidence=SituationEvidence(
            facts={"event_a_id": key},
            quoted_external={"subject": UNTRUSTED_SUBJECT},
        ),
    )


def deliver_one(harness: ToolHarness, key: str) -> SituationResponse:
    """Detect one situation and let the harness claim it for delivery."""
    _ = harness.store.situations.upsert_detections((pending_detection(key),))
    claimed = harness.service.proactive_check().situations
    assert len(claimed) == 1
    return claimed[0]


def tool_schema(tool: Tool) -> ToolSchema:
    """Parse one advertised MCP tool schema into its asserted fields."""
    return ToolSchema.model_validate(tool.input_schema)


def error_text(result: CallToolResult) -> str:
    """Return what one refused tool call reported back to the agent."""
    assert result.is_error is True
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def check_in_worker(
    state_directory: Path,
    now: datetime,
    barrier: Barrier,
) -> ProactiveCheckResponse:
    """Check from one instance whose claim races every rival's claim."""
    with open_harness(state_directory, now) as harness:
        racing = SituationToolService(
            replace(harness.dependencies, clock=BarrierClock(harness.clock, barrier))
        )
        return racing.proactive_check()
