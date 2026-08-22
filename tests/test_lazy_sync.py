from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from proactive_mcp.delivery.evaluation import (
    EvaluationDependencies,
    EvaluationService,
    PreparedSources,
    SkippedSources,
)
from proactive_mcp.paths import resolve_paths
from proactive_mcp.situations.engine import SituationEngine
from proactive_mcp.sources.credentials import CredentialStore
from proactive_mcp.sources.lazy_sync import (
    LazySourceProvider,
    LazySyncPolicy,
    ScheduledSourceProvider,
    SourceAccess,
    open_source_access,
)
from proactive_mcp.store import Store
from tests.daemon_test_support import (
    FakeCredential,
    FakeCredentialStore,
    FakeReaderFactory,
    StoreBackedReader,
    birthday_memory,
    stale_reply_thread,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

_PID = 4242
_POLL_INTERVAL = timedelta(minutes=5)
_STALE = timedelta(hours=25)
_START = utc_datetime(2026, 8, 21, 12)


@dataclass(frozen=True, slots=True)
class _Degraded:
    """One degraded-mode wiring with its seams exposed for interleaving."""

    service: EvaluationService
    provider: LazySourceProvider
    evaluator: SituationEngine
    reader: StoreBackedReader


def _degraded(
    store: Store,
    clock: FakeClock,
    reader: StoreBackedReader,
) -> _Degraded:
    provider = LazySourceProvider(
        access=SourceAccess(
            sync_state=store,
            credentials=FakeCredentialStore(FakeCredential()),
            readers=FakeReaderFactory(reader=reader),
        ),
        liveness=store.daemon,
        clock=clock,
        policy=LazySyncPolicy.for_poll_interval(_POLL_INTERVAL),
    )
    evaluator = SituationEngine(store, clock, UTC)
    return _Degraded(
        service=EvaluationService(
            EvaluationDependencies(evaluator=evaluator, sources=provider)
        ),
        provider=provider,
        evaluator=evaluator,
        reader=reader,
    )


def _synced_store(store: Store, clock: FakeClock, age: timedelta) -> None:
    """Record one successful sync of both sources and let it age."""
    store.record_sync_success("gmail")
    store.record_sync_success("calendar")
    clock.advance(age)


def test_lazy_sync_reads_sources_when_no_daemon_ran_and_data_is_stale(
    tmp_path: Path,
) -> None:
    # Given: configured sources whose last success aged past the stale window.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, _STALE)
        degraded = _degraded(
            store,
            clock,
            StoreBackedReader(store=store, threads=(stale_reply_thread(clock.now()),)),
        )

        # When: proactive_check evaluates with no daemon heartbeat on record.
        completed = degraded.service.run_once()
        detected = store.situations.list_situations()

    # Then: the inline read happened and its truth reached the engine.
    assert isinstance(completed.sources, PreparedSources)
    assert degraded.reader.reads == [1]
    assert completed.result.created == 1
    assert tuple(item.situation_type for item in detected) == ("reply_deadline",)


def test_lazy_sync_is_skipped_while_a_daemon_heartbeat_is_current(
    tmp_path: Path,
) -> None:
    # Given: stale source data and a daemon that started this instant.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, _STALE)
        store.daemon.record_start(pid=_PID)
        degraded = _degraded(store, clock, StoreBackedReader(store=store))

        # When: a tool call evaluates while the watcher owns source reads.
        completed = degraded.service.run_once()

    # Then: no duplicate inline read runs, and staleness is still reported.
    assert completed.sources == SkippedSources("daemon_running")
    assert degraded.reader.reads == []
    assert "gmail: source is stale" in completed.warnings
    assert [text for text in completed.warnings if text.startswith("google:")] == []


def test_lazy_sync_resumes_once_the_daemon_heartbeat_goes_stale(
    tmp_path: Path,
) -> None:
    # Given: a daemon that started and then stopped emitting heartbeats.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, timedelta())
        store.daemon.record_start(pid=_PID)
        clock.advance(_STALE)
        degraded = _degraded(store, clock, StoreBackedReader(store=store))

        # When: a tool call evaluates after the watcher went silent.
        completed = degraded.service.run_once()

    # Then: degraded mode takes the read back over.
    assert isinstance(completed.sources, PreparedSources)
    assert degraded.reader.reads == [1]


def test_lazy_sync_is_skipped_while_both_sources_are_fresh(tmp_path: Path) -> None:
    # Given: both sources synced one hour ago.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, timedelta(hours=1))
        degraded = _degraded(store, clock, StoreBackedReader(store=store))

        # When: a tool call evaluates fresh state.
        completed = degraded.service.run_once()

    # Then: no read is spent on data that is already current.
    assert completed.sources == SkippedSources("already_fresh")
    assert degraded.reader.reads == []
    assert completed.warnings == ()


def test_lazy_sync_yields_to_a_read_attempt_another_instance_just_made(
    tmp_path: Path,
) -> None:
    # Given: stale data plus a failed read attempt one minute ago.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, _STALE)
        store.record_sync_failure("gmail", error_code="http_5xx")
        clock.advance(timedelta(minutes=1))
        degraded = _degraded(store, clock, StoreBackedReader(store=store))

        # When: a second instance evaluates inside the attempt interval.
        completed = degraded.service.run_once()

    # Then: it defers the read and still warns about the failing source.
    assert completed.sources == SkippedSources("sync_in_flight")
    assert degraded.reader.reads == []
    assert "gmail: source is error" in completed.warnings
    assert (
        "google: another read attempt is in flight; read skipped"
    ) in completed.warnings


def test_missing_credentials_still_detect_the_local_birthday(tmp_path: Path) -> None:
    # Given: configured sources with no stored credential and a D-7 memory.
    clock = FakeClock(utc_datetime(2026, 7, 11, 9))
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        store.set_google_auth_state("configured")
        _ = store.remember(birthday_memory())
        reader = StoreBackedReader(store=store)
        service = EvaluationService(
            EvaluationDependencies(
                evaluator=SituationEngine(store, clock, UTC),
                sources=LazySourceProvider(
                    access=SourceAccess(
                        sync_state=store,
                        credentials=FakeCredentialStore(),
                        readers=FakeReaderFactory(reader=reader),
                    ),
                    liveness=store.daemon,
                    clock=clock,
                    policy=LazySyncPolicy.for_poll_interval(_POLL_INTERVAL),
                ),
            )
        )

        # When: a tool call evaluates without any Google access.
        completed = service.run_once()
        detected = store.situations.list_situations()

    # Then: the memory-based occasion is detected without a remote read.
    assert completed.sources == SkippedSources("missing_credentials")
    assert reader.reads == []
    assert tuple(item.situation_type for item in detected) == ("personal_occasion",)
    assert detected[0].priority == "high"
    assert "gmail: source is never_synced" in completed.warnings
    assert (
        "google: stored credentials are missing; run proactive-mcp setup"
    ) in completed.warnings


def test_unconfigured_sources_skip_reads_without_an_all_clear(tmp_path: Path) -> None:
    # Given: an installation whose Google setup never ran.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        degraded = _degraded(store, clock, StoreBackedReader(store=store))

        # When: a tool call evaluates unconfigured sources.
        completed = degraded.service.run_once()

    # Then: the reason is named and the empty result carries warnings.
    assert completed.sources == SkippedSources("not_configured")
    assert degraded.reader.reads == []
    assert "gmail: source is not_configured" in completed.warnings
    assert "calendar: source is not_configured" in completed.warnings
    assert (
        "google: sources are not configured; run proactive-mcp setup"
    ) in completed.warnings


def test_revoked_grant_skips_reads_until_reauthorization(tmp_path: Path) -> None:
    # Given: a shared Google grant Google has revoked.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, _STALE)
        store.record_google_invalid_grant()
        degraded = _degraded(store, clock, StoreBackedReader(store=store))

        # When: a tool call evaluates with the revoked grant.
        completed = degraded.service.run_once()

    # Then: reauthorization is named instead of a retry with dead credentials.
    assert completed.sources == SkippedSources("needs_reauth")
    assert degraded.reader.reads == []
    assert (
        "google: authorization is no longer valid; run proactive-mcp setup --reauth"
    ) in completed.warnings


def test_scheduled_watcher_reads_sources_even_while_they_are_fresh(
    tmp_path: Path,
) -> None:
    # Given: fresh sources and the watcher-side provider.
    clock = FakeClock(_START)
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _synced_store(store, clock, timedelta(hours=1))
        reader = StoreBackedReader(store=store)
        provider = ScheduledSourceProvider(
            access=SourceAccess(
                sync_state=store,
                credentials=FakeCredentialStore(FakeCredential()),
                readers=FakeReaderFactory(reader=reader),
            )
        )

        # When: the daemon prepares its scheduled pass.
        outcome = provider.prepare_sources()

    # Then: polling cadence, not freshness, decides the daemon's reads.
    assert isinstance(outcome, PreparedSources)
    assert reader.reads == [1]


def test_concurrent_lazy_reads_cannot_corrupt_source_generations(
    tmp_path: Path,
) -> None:
    # Given: two server instances that both pass the degraded gate.
    database = tmp_path / "proactive.db"
    clock = FakeClock(_START)
    with (
        Store(database, clock=clock) as older_store,
        Store(database, clock=clock) as newer_store,
    ):
        _synced_store(older_store, clock, _STALE)
        older = _degraded(
            older_store,
            clock,
            StoreBackedReader(
                store=older_store,
                threads=(stale_reply_thread(clock.now()),),
            ),
        )
        newer = _degraded(newer_store, clock, StoreBackedReader(store=newer_store))

        # When: the older instance reads first but applies last.
        older_sources = older.provider.prepare_sources()
        newer_completed = newer.service.run_once()
        assert isinstance(older_sources, PreparedSources)
        older_result = older.evaluator.evaluate(older_sources.inputs)
        generation = older_store.source_generation_state("gmail")
        situations = older_store.situations.list_situations()

    # Then: the delayed read is ignored instead of resurrecting old truth.
    assert isinstance(newer_completed.sources, PreparedSources)
    assert "gmail: delayed source generation ignored" in older_result.warnings
    assert older_result.created == 0
    assert generation.applied == 2
    assert situations == ()


def test_source_access_keeps_credentials_beside_the_database(tmp_path: Path) -> None:
    # Given: an installation whose database lives in an isolated state directory.
    clock = FakeClock(_START)
    paths = resolve_paths({"PROACTIVE_DATABASE": str(tmp_path / "state" / "db")})
    with Store(paths.database, clock=clock) as store:
        # When: production source access is composed for that installation.
        access = open_source_access(paths, store, clock)

    # Then: credential storage stays under the resolved state directory.
    credentials = access.credentials
    assert isinstance(credentials, CredentialStore)
    assert credentials.file_path.parent.parent == paths.state_directory
    assert access.sync_state is store
