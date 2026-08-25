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
    assert delayed_pass.sources.inputs.gmail_diagnostics == older_diagnostics
    assert delayed_pass.result.accepted_gmail_diagnostics == newer_diagnostics
    assert "gmail: delayed source generation ignored" in delayed_pass.warnings
    assert persisted == newer_diagnostics


def test_split_source_health_reads_reproduce_impossible_mixture(tmp_path: Path) -> None:
    # Given: the old split reads straddle an atomic failure commit on connection two.
    database = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    healthy = SourceReadDiagnostics("healthy", 1, 1, 0, 0, 8_000_000)
    failure = SourceReadDiagnostics(
        "transport_error",
        0,
        0,
        0,
        0,
        8_000_000,
        (SourceReadReasonCount("network", 1),),
    )
    state_read = Event()
    failure_committed = Event()
    with Store(database, clock=clock) as reader:
        reader.record_gmail_sync(healthy)
        reader.record_sync_success("calendar")

        def commit_failure() -> None:
            with Store(database, clock=clock) as writer:
                assert state_read.wait(timeout=10)
                writer.record_gmail_sync(failure, error_code="network")
                failure_committed.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            committed = executor.submit(commit_failure)
            old_gmail, _ = reader.list_source_sync()
            state_read.set()
            assert failure_committed.wait(timeout=10)
            old_diagnostics = reader.gmail_diagnostics()
            committed.result(timeout=10)

    # Then: the split contract reports a state that never existed.
    assert old_gmail.last_error_code is None
    assert old_diagnostics == failure


def test_source_health_snapshot_is_coherent_during_commit(tmp_path: Path) -> None:
    # Given: a healthy snapshot and a failure ready on connection two.
    database = tmp_path / "situations.db"
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    healthy = SourceReadDiagnostics("healthy", 1, 1, 0, 0, 8_000_000)
    failure = SourceReadDiagnostics(
        "transport_error",
        0,
        0,
        0,
        0,
        8_000_000,
        (SourceReadReasonCount("network", 1),),
    )
    snapshot_started = Event()
    concurrent_failure_committed = Event()
    progress_calls = 0
    with Store(database, clock=clock) as reader:
        reader.record_gmail_sync(healthy)
        reader.record_sync_success("calendar")

        def commit_concurrent_failure() -> None:
            with Store(database, clock=clock) as writer:
                assert snapshot_started.wait(timeout=10)
                writer.record_gmail_sync(failure, error_code="network")
                concurrent_failure_committed.set()

        def pause_snapshot() -> int:
            nonlocal progress_calls
            progress_calls += 1
            if progress_calls == 1:
                snapshot_started.set()
                assert concurrent_failure_committed.wait(timeout=10)
            return 0

        with ThreadPoolExecutor(max_workers=1) as executor:
            committed = executor.submit(commit_concurrent_failure)
            reader.connection().set_progress_handler(pause_snapshot, 5)
            try:
                snapshot = reader.source_health_snapshot()
            finally:
                reader.connection().set_progress_handler(None, 0)
            committed.result(timeout=10)
        persisted = reader.source_health_snapshot()

    # Then: the active and subsequent snapshots each belong to one commit.
    assert progress_calls > 1
    assert snapshot.gmail.last_error_code is None
    assert snapshot.gmail_diagnostics == healthy
    assert persisted.gmail.last_error_code == "network"
    assert persisted.gmail_diagnostics == failure
