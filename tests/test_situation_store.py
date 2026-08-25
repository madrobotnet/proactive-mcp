from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from threading import Barrier, Event
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.delivery.evaluation import (
    EvaluationDependencies,
    EvaluationPass,
    EvaluationService,
    PreparedSources,
)
from proactive_mcp.situations.engine import SituationEngine
from proactive_mcp.situations.inputs import EngineInputs, SourceSnapshot
from proactive_mcp.store import Store
from proactive_mcp.store.sync import (
    SourceReadDiagnostics,
    SourceReadReason,
    SourceReadReasonCount,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path


def _diagnostics(
    request_count: int,
    reason: SourceReadReason,
) -> SourceReadDiagnostics:
    return SourceReadDiagnostics(
        outcome="partial",
        request_count=request_count,
        page_count=1,
        projected_count=1,
        excluded_count=1,
        byte_budget=8_000_000,
        reason_counts=(SourceReadReasonCount(reason, request_count),),
    )


def test_gmail_diagnostic_transaction_failure_preserves_prior_row(
    tmp_path: Path,
) -> None:
    # Given: one accepted diagnostic row and a trigger interrupting replacement.
    database = tmp_path / "situations.db"
    prior = _diagnostics(2, "body_truncated")
    replacement = _diagnostics(9, "pagination_limit")
    with Store(database) as store:
        store.record_gmail_sync(prior, error_code="degraded")
        _ = store.connection().execute(
            """
            CREATE TRIGGER interrupt_gmail_diagnostic_reasons
            BEFORE INSERT ON gmail_diagnostic_reason_counts
            WHEN NEW.reason = 'pagination_limit'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic interruption');
            END
            """
        )

        # When: SQLite aborts after the replacement transaction has begun.
        with pytest.raises(sqlite3.IntegrityError, match="synthetic interruption"):
            store.record_gmail_sync(replacement, error_code="degraded")
        persisted = store.gmail_diagnostics()
        freshness = store.get_source_sync("gmail")

    # Then: rollback retains the complete prior diagnostic and freshness row.
    assert persisted == prior
    assert freshness.last_error_code == "degraded"


@dataclass(frozen=True, slots=True)
class _FixedSources:
    inputs: EngineInputs

    def prepare_sources(self) -> PreparedSources:
        return PreparedSources(self.inputs)


def test_rejected_delayed_generation_cannot_leak_candidate_diagnostics(
    tmp_path: Path,
) -> None:
    # Given: two stores reserve Gmail generations with distinct aggregate results.
    database = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    start = Barrier(3)
    newer_accepted = Event()
    older_diagnostics = _diagnostics(91, "body_truncated")
    newer_diagnostics = _diagnostics(4, "pagination_limit")
    with Store(database, clock=clock) as reservation_store:
        older_generation = reservation_store.reserve_source_generation("gmail")
        newer_generation = reservation_store.reserve_source_generation("gmail")
    older_inputs = EngineInputs(
        gmail_threads=SourceSnapshot(
            generation=older_generation,
            items=(),
            complete=False,
        ),
        gmail_diagnostics=older_diagnostics,
    )
    newer_inputs = EngineInputs(
        gmail_threads=SourceSnapshot(
            generation=newer_generation,
            items=(),
            complete=False,
        ),
        gmail_diagnostics=newer_diagnostics,
    )

    def run_older() -> EvaluationPass:
        with Store(database, clock=clock) as older_store:
            service = EvaluationService(
                EvaluationDependencies(
                    SituationEngine(older_store, clock, UTC),
                    _FixedSources(older_inputs),
                )
            )
            assert start.wait(timeout=10) >= 0
            assert newer_accepted.wait(timeout=10)
            return service.run_once()

    def run_newer() -> EvaluationPass:
        with Store(database, clock=clock) as newer_store:
            service = EvaluationService(
                EvaluationDependencies(
                    SituationEngine(newer_store, clock, UTC),
                    _FixedSources(newer_inputs),
                )
            )
            assert start.wait(timeout=10) >= 0
            result = service.run_once()
            newer_accepted.set()
            return result

    # When: both workers start together but generation two commits first.
    with ThreadPoolExecutor(max_workers=2) as executor:
        older_future = executor.submit(run_older)
        newer_future = executor.submit(run_newer)
        assert start.wait(timeout=10) >= 0
        newer_pass = newer_future.result(timeout=10)
        delayed_pass = older_future.result(timeout=10)
    with Store(database, clock=clock) as reopened:
        persisted = reopened.gmail_diagnostics()

    # Then: store and both public pass results expose only accepted generation two.
    assert isinstance(newer_pass.sources, PreparedSources)
    assert isinstance(delayed_pass.sources, PreparedSources)
    assert newer_pass.sources.inputs.gmail_diagnostics == newer_diagnostics
    assert delayed_pass.sources.inputs.gmail_diagnostics == newer_diagnostics
    assert delayed_pass.result.gmail_diagnostics == newer_diagnostics
    assert "gmail: delayed source generation ignored" in delayed_pass.warnings
    assert persisted == newer_diagnostics
    assert "91" not in repr(delayed_pass)
