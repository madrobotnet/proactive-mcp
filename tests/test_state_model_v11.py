from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

import proactive_mcp.server.status as status_module
import proactive_mcp.sources as sources_module
from proactive_mcp.delivery import EvaluationDependencies, EvaluationService
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.situation_tools import SituationToolService
from proactive_mcp.server.status import status_response
from proactive_mcp.situations import AttentionPolicy
from proactive_mcp.situations.inputs import EngineInputs, SourceSnapshot
from proactive_mcp.sources.credentials import CredentialStore
from proactive_mcp.store import FallbackClaim, Store
from proactive_mcp.store.sync import (
    GMAIL_READ_BYTE_BUDGET,
    SourceReadDiagnostics,
    SourceReadReasonCount,
)
from tests.cli_oauth_test_support import FakeKeyring
from tests.situation_test_support import FakeClock, utc_datetime
from tests.situation_tool_support import (
    FixedSources,
    deliver_one,
    open_harness,
    pending_detection,
)

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from proactive_mcp.store import DeliveryReservation

_GENERATION_SNAPSHOT_FAILURE = "generation must come from source snapshot"
_CHECK_FAILURE = "injected proactive check failure"


class _FailedEvaluation:
    def run_once(self) -> None:
        raise RuntimeError(_CHECK_FAILURE)


def test_status_separates_source_authorization_freshness_and_read_state(
    tmp_path: Path,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    diagnostics = SourceReadDiagnostics(
        outcome="partial",
        request_count=8,
        page_count=2,
        projected_count=3,
        excluded_count=5,
        byte_budget=8_000_000,
        reason_counts=(SourceReadReasonCount("pagination_limit", 1),),
    )
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_gmail_sync(diagnostics, error_code="degraded")
        store.record_sync_success("calendar")

        status = status_response(store, clock, paths)

    assert status.google.gmail.status == "error"
    assert status.google.gmail.authorization.state == "configured"
    assert status.google.gmail.freshness.state == "never_synced"
    assert status.google.gmail.read.state == "partial"
    assert status.google.calendar.authorization.state == "configured"
    assert status.google.calendar.freshness.state == "fresh"
    assert status.google.calendar.read.state == "complete"


def test_scope_mismatch_is_authorization_state_not_generic_source_error(
    tmp_path: Path,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_failure("gmail", error_code="scope_mismatch")

        status = status_response(store, clock, paths)

    assert status.google.gmail.authorization.state == "scope_mismatch"
    assert status.google.gmail.read.state == "auth_error"


def test_proactive_check_structures_temporary_credential_unavailability(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(
        tmp_path,
        now,
        sources="credential_storage_unavailable",
    ) as harness:
        harness.store.set_google_auth_state("configured")

        response = harness.service.proactive_check()

    assert response.freshness.gmail.authorization.state == "credential_unavailable"
    assert response.freshness.calendar.authorization.state == "credential_unavailable"
    assert any(
        "credential storage is unavailable" in item for item in response.warnings
    )


def test_credential_unavailability_survives_coalesced_checks_and_status(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(
        tmp_path,
        now,
        sources="credential_storage_unavailable",
    ) as harness:
        harness.store.set_google_auth_state("configured")

        first = harness.service.proactive_check()
        second = harness.service.proactive_check()
        paths = harness.paths

    with Store(paths.database, clock=FakeClock(now)) as reopened:
        observed = status_response(
            reopened,
            FakeClock(now),
            paths,
        )

    assert first.freshness.gmail.authorization.state == "credential_unavailable"
    assert second.freshness.gmail.authorization.state == "credential_unavailable"
    assert observed.google.gmail.authorization.state == "credential_unavailable"


def test_missing_credentials_survive_coalesced_checks_and_status(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(
        tmp_path,
        now,
        sources="missing_credentials",
    ) as harness:
        harness.store.set_google_auth_state("configured")

        first = harness.service.proactive_check()
        second = harness.service.proactive_check()
        paths = harness.paths

    with Store(paths.database, clock=FakeClock(now)) as reopened:
        observed = status_response(
            reopened,
            FakeClock(now),
            paths,
        )

    assert first.freshness.gmail.authorization.state == "credential_missing"
    assert second.freshness.gmail.authorization.state == "credential_missing"
    assert observed.google.gmail.authorization.state == "credential_missing"


def test_disconnect_clears_credential_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))

    def credential_store(path: Path) -> CredentialStore:
        return CredentialStore(path, keyring=FakeKeyring())

    monkeypatch.setattr(
        sources_module,
        "CredentialStore",
        credential_store,
    )
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_credential_state("missing")

    sources_module.disconnect_google_sources(paths.database)

    with Store(paths.database, clock=clock) as store:
        snapshot = store.source_health_snapshot()
        observed = status_response(store, clock, paths)

    assert snapshot.credential.state == "unknown"
    assert observed.google.gmail.status == "not_configured"
    assert observed.google.gmail.authorization.state == "not_configured"


def test_freshness_age_remains_stale_after_a_newer_transport_error(
    tmp_path: Path,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        clock.advance(timedelta(hours=25))
        store.record_sync_failure("gmail", error_code="network")

        gmail = status_response(store, clock, paths).google.gmail

    assert gmail.status == "error"
    assert gmail.freshness.state == "stale"
    assert gmail.read.state == "transport_error"


def test_status_exposes_current_syncing_interrupted_and_degraded_generations(
    tmp_path: Path,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        current = status_response(store, clock, paths).google.gmail.generation

        generation = store.reserve_source_generation("gmail")
        syncing = status_response(store, clock, paths).google.gmail.generation

        clock.advance(timedelta(minutes=11))
        interrupted = status_response(store, clock, paths).google.gmail.generation

        _ = store.situations.apply_source_generation(
            generation,
            (),
            status="degraded",
            error_code="network",
        )
        degraded = status_response(store, clock, paths).google.gmail.generation

    assert current.model_dump() == {
        "state": "current",
        "issued": 0,
        "applied": 0,
        "applied_status": None,
        "issued_at": None,
    }
    assert syncing.state == "syncing"
    assert (syncing.issued, syncing.applied) == (1, 0)
    assert syncing.issued_at is not None
    assert interrupted.state == "interrupted"
    assert degraded.state == "degraded"
    assert degraded.applied_status == "degraded"


def test_status_uses_one_source_snapshot_without_followup_generation_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        generation = store.reserve_source_generation("gmail")
        _ = store.situations.apply_source_generation(
            generation,
            (),
            status="complete",
        )

        def reject_followup(
            _store: Store,
            _source: object,
        ) -> object:
            raise AssertionError(_GENERATION_SNAPSHOT_FAILURE)

        monkeypatch.setattr(Store, "source_generation_state", reject_followup)

        status = status_response(store, clock, paths)

    assert status.google.gmail.generation.state == "current"
    assert status.google.gmail.generation.issued == generation.number


def test_proactive_response_uses_one_post_evaluation_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        harness.store.set_google_auth_state("configured")
        gmail = harness.store.reserve_source_generation("gmail")
        calendar = harness.store.reserve_source_generation("calendar")
        evaluation = EvaluationService(
            EvaluationDependencies(
                evaluator=harness.dependencies.runtime.engine,
                sources=FixedSources(
                    EngineInputs(
                        gmail_threads=SourceSnapshot(
                            gmail,
                            (),
                            resolve_absent=True,
                        ),
                        calendar_events=SourceSnapshot(
                            calendar,
                            (),
                            resolve_absent=True,
                        ),
                        gmail_diagnostics=SourceReadDiagnostics(
                            outcome="healthy",
                            request_count=0,
                            page_count=0,
                            projected_count=0,
                            excluded_count=0,
                            byte_budget=GMAIL_READ_BYTE_BUDGET,
                        ),
                    )
                ),
            )
        )
        service = SituationToolService(
            replace(harness.dependencies, evaluation=evaluation)
        )
        with Store(harness.paths.database, clock=harness.clock) as writer:
            original_reserve = AttentionPolicy.reserve_for_delivery
            interleaved = False

            def interleave_after_evaluation(
                policy: AttentionPolicy,
                evaluated_at: datetime,
            ) -> DeliveryReservation:
                nonlocal interleaved
                reservation = original_reserve(policy, evaluated_at)
                if not interleaved:
                    interleaved = True
                    generation = writer.reserve_source_generation("gmail")
                    _ = writer.situations.apply_source_generation(
                        generation,
                        (),
                        status="degraded",
                        error_code="network",
                    )
                return reservation

            monkeypatch.setattr(
                AttentionPolicy,
                "reserve_for_delivery",
                interleave_after_evaluation,
            )

            response = service.proactive_check()

    assert response.freshness.gmail.status == "error"
    assert response.freshness.gmail.generation.state == "degraded"
    assert "gmail: source is error" in response.warnings
    assert response.all_clear is False


def test_situation_delivery_state_distinguishes_available_leased_and_confirmed(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("delivery-state"),)
        )
        available = harness.service.list_situations("pending").items[0]

        checked = harness.service.proactive_check(profile="full")
        leased = checked.situations[0]
        listed_while_leased = harness.service.list_situations("pending").items[0]
        assert checked.receipt_token is not None
        _ = harness.service.confirm_delivery(checked.receipt_token, profile="full")
        confirmed = harness.service.get_situation(leased.id)

    assert available.delivery.state == "available"
    assert available.delivery.lease_expires_at is None
    assert leased.delivery.state == "leased"
    assert leased.delivery.lease_expires_at is not None
    assert listed_while_leased.delivery.state == "leased"
    assert confirmed.state == "delivered"
    assert confirmed.delivery.state == "host_confirmed"
    assert confirmed.delivery.presentation == "unknown"


def test_woken_pending_situation_reports_current_lease_not_old_confirmation(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        _ = harness.store.situations.upsert_detections(
            (pending_detection("wake-delivery-state"),)
        )
        first = harness.service.proactive_check()
        assert first.receipt_token is not None
        _ = harness.service.confirm_delivery(first.receipt_token)
        _ = harness.service.snooze_situation(
            first.situations[0].id,
            (harness.clock.now() + timedelta(minutes=1)).isoformat(),
        )

        harness.clock.advance(timedelta(minutes=2))
        repeated = harness.service.proactive_check()

    assert repeated.receipt_token is not None
    assert repeated.situations[0].state == "pending"
    assert repeated.situations[0].delivery.state == "leased"


def test_terminal_state_does_not_reuse_prior_cycle_confirmation(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        delivered = deliver_one(harness, "delivery-cycle")
        _ = harness.service.snooze_situation(
            delivered.id,
            (harness.clock.now() + timedelta(minutes=1)).isoformat(),
        )
        harness.clock.advance(timedelta(minutes=2))

        leased = harness.service.proactive_check()
        assert leased.receipt_token is not None
        _ = harness.store.situations.resolve_absent("calendar_conflict", ())
        terminal = harness.service.get_situation(delivered.id)

    assert terminal.state == "resolved"
    assert terminal.delivery.state == "not_applicable"


def test_source_backed_situation_exposes_generation_provenance(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        generation = harness.store.reserve_source_generation("gmail")
        detection = replace(
            pending_detection("source-provenance"),
            situation_type="reply_deadline",
        )
        _ = harness.store.situations.apply_source_generation(
            generation,
            (detection,),
            status="complete",
        )

        item = harness.service.list_situations("pending").items[0]

    assert item.source.name == "gmail"
    assert item.source.generation == generation.number


def test_complete_source_generation_does_not_resolve_local_provenance(
    tmp_path: Path,
) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        local = replace(
            pending_detection("local-reply-deadline"),
            situation_type="reply_deadline",
        )
        _ = store.situations.upsert_detections((local,))
        generation = store.reserve_source_generation("gmail")

        _ = store.situations.apply_source_generation(
            generation,
            (),
            status="complete",
        )
        situation = store.situations.list_situations()[0]

    assert situation.source_name == "local"
    assert situation.state == "pending"


def test_collector_status_tracks_profile_check_confirmation_and_staleness(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        before = status_response(
            harness.store,
            harness.clock,
            harness.paths,
        ).collectors
        _ = harness.store.situations.upsert_detections(
            (pending_detection("collector-state"),)
        )

        checked = harness.service.proactive_check(profile="full")
        assert checked.receipt_token is not None
        _ = harness.service.confirm_delivery(
            checked.receipt_token,
            profile="full",
        )
        active = status_response(
            harness.store,
            harness.clock,
            harness.paths,
        ).collectors

        harness.clock.advance(timedelta(hours=25))
        stale = status_response(
            harness.store,
            harness.clock,
            harness.paths,
        ).collectors

    assert before.full.state == "never_seen"
    assert before.scheduled.state == "never_seen"
    assert active.full.state == "active"
    assert active.full.last_check_at is not None
    assert active.full.last_confirm_at is not None
    assert active.scheduled.state == "never_seen"
    assert stale.full.state == "stale"


def test_collector_observation_timestamps_never_regress(
    tmp_path: Path,
) -> None:
    database = tmp_path / "proactive.db"
    newer = FakeClock(utc_datetime(2026, 8, 29, 13))
    older = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(database, clock=newer) as store:
        store.collectors.record_check("scheduled")
        store.collectors.record_confirm("scheduled")
    with Store(database, clock=older) as store:
        store.collectors.record_check("scheduled")
        store.collectors.record_confirm("scheduled")
        observed = store.collectors.status("scheduled")

    assert observed.last_check_at == newer.now().isoformat()
    assert observed.last_confirm_at == newer.now().isoformat()


def test_failed_proactive_check_does_not_activate_collector(
    tmp_path: Path,
) -> None:
    with open_harness(tmp_path, utc_datetime(2026, 8, 29, 12)) as harness:
        service = SituationToolService(
            replace(harness.dependencies, evaluation=_FailedEvaluation())
        )

        with pytest.raises(RuntimeError, match=_CHECK_FAILURE):
            _ = service.proactive_check(profile="scheduled")

        observed = harness.store.collectors.status("scheduled")

    assert observed.state == "never_seen"
    assert observed.last_check_at is None


def test_daemon_status_persists_last_run_mode_and_bounded_failure(
    tmp_path: Path,
) -> None:
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        before = store.daemon.status()
        store.daemon.record_start(pid=4242)
        store.daemon.record_run_started("once")
        store.daemon.record_run_outcome(
            "failed",
            failure_phase="credential",
            failure_code="unavailable",
        )
        store.daemon.record_stop()
        after = store.daemon.status()

    assert before.last_run_state == "never_run"
    assert before.mode is None
    assert after.liveness == "stopped"
    assert after.mode == "once"
    assert after.last_run_state == "failed"
    assert after.last_failure_phase == "credential"
    assert after.last_failure_code == "unavailable"
    assert after.last_failure_at == clock.now().isoformat()


def test_fallback_status_distinguishes_disabled_unavailable_and_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    _ = paths.config.write_text("[fallback]\nenabled = false\n", encoding="utf-8")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(paths.database, clock=clock) as store:
        disabled = status_module.status_response(store, clock, paths).fallback

        _ = paths.config.write_text("[fallback]\nenabled = true\n", encoding="utf-8")
        monkeypatch.setattr(status_module, "notification_available", lambda: False)
        unavailable = status_module.status_response(store, clock, paths).fallback

        monkeypatch.setattr(status_module, "notification_available", lambda: True)
        healthy = status_module.status_response(store, clock, paths).fallback

    assert disabled.state == "disabled"
    assert unavailable.state == "unavailable"
    assert healthy.state == "healthy"


def test_fallback_unavailability_takes_precedence_over_failure_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 29, 12))
    with Store(paths.database, clock=clock) as store:
        _ = store.situations.upsert_detections(
            (pending_detection("fallback-precedence", priority="critical"),)
        )
        clock.advance(timedelta(minutes=31))
        claim = store.fallbacks.claim_next(
            FallbackClaim(
                claimed_at=clock.now().isoformat(),
                detected_before=(clock.now() - timedelta(minutes=30)).isoformat(),
                priorities=("critical",),
            )
        )
        assert claim is not None
        store.fallbacks.record_failed(claim.id, code="tool_missing")
        monkeypatch.setattr(status_module, "notification_available", lambda: False)

        fallback = status_module.status_response(store, clock, paths).fallback

    assert fallback.state == "unavailable"
    assert fallback.history_state == "degraded"


def test_database_health_and_receipt_failure_are_machine_readable(
    tmp_path: Path,
) -> None:
    now = utc_datetime(2026, 8, 29, 12)
    with open_harness(tmp_path, now) as harness:
        database = status_response(
            harness.store,
            harness.clock,
            harness.paths,
        ).database
        receipt = harness.service.confirm_delivery(
            "unknown-or-expired-receipt",
            profile="full",
        )

    assert database.status == "healthy"
    assert database.health == "ready"
    assert database.migration_version == 12
    assert receipt.status == "invalid_or_expired"
    assert receipt.delivered_count == 0
