from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import proactive_mcp.store._situation_upsert as upsert_module
from proactive_mcp.delivery import EvaluationDependencies, EvaluationService
from proactive_mcp.server.situation_tools import SituationToolService
from proactive_mcp.situations.inputs import EngineInputs, SourceSnapshot
from proactive_mcp.store import Detection, SituationEvidence
from proactive_mcp.store.sync import SourceReadDiagnostics, SourceReadReasonCount
from tests.daemon_test_support import birthday_memory
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import FixedSources as _FixedSources
from tests.situation_tool_support import open_harness, pending_detection

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_BIRTHDAY_MORNING = utc_datetime(2026, 7, 11, 9)
_NOON = utc_datetime(2026, 8, 21, 12)


def test_proactive_check_uses_latest_accepted_diagnostics_live_coalesced_and_reopened(
    tmp_path: Path,
) -> None:
    accepted = SourceReadDiagnostics(
        outcome="partial",
        request_count=17,
        page_count=3,
        projected_count=5,
        excluded_count=12,
        byte_budget=8_000_000,
        reason_counts=(SourceReadReasonCount("pagination_limit", 2),),
    )
    with open_harness(tmp_path, _NOON) as harness:
        generation = harness.store.reserve_source_generation("gmail")
        inputs = EngineInputs(
            gmail_threads=SourceSnapshot(
                generation=generation,
                items=(),
                complete=False,
            ),
            gmail_diagnostics=accepted,
        )
        live_service = SituationToolService(
            replace(
                harness.dependencies,
                evaluation=EvaluationService(
                    EvaluationDependencies(
                        evaluator=harness.dependencies.runtime.engine,
                        sources=_FixedSources(inputs),
                    )
                ),
            )
        )

        live = live_service.proactive_check()
        persisted = harness.store.gmail_diagnostics()
        coalesced = live_service.proactive_check()

    with open_harness(tmp_path, _NOON, "already_fresh") as reopened_harness:
        reopened = reopened_harness.service.proactive_check()

    assert persisted == accepted
    for response in (live, coalesced, reopened):
        assert response.freshness.gmail.diagnostics.model_dump() == {
            "outcome": "partial",
            "request_count": 17,
            "page_count": 3,
            "projected_count": 5,
            "excluded_count": 12,
            "byte_budget": 8_000_000,
            "reason_counts": {"pagination_limit": 2},
        }


def test_proactive_check_excludes_gmail_after_newer_failed_generation(
    tmp_path: Path,
) -> None:
    # Given: a complete Gmail generation produced one pending reply deadline.
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        gmail = Detection(
            situation_type="reply_deadline",
            dedupe_key="gmail-generation-row",
            priority="routine",
            title="Fixture reply deadline",
            why_now="Fixture delivery candidate",
            evidence=SituationEvidence(facts={"thread_id": "generation-thread"}),
        )
        first_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            first_generation,
            (gmail,),
            status="complete",
        )
        _ = harness.store.situations.upsert_detections(
            (pending_detection("independent-calendar"),)
        )

        # When: a newer Gmail generation fails before proactive_check claims rows.
        failed_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            failed_generation,
            (),
            status="degraded",
            error_code="network",
        )
        failed_response = harness.service.proactive_check()
        stored_during_failure = harness.store.situations.list_situations(limit=10)
        failed_state = harness.store.source_generation_state("gmail")
        assert failed_response.receipt_token is not None
        _ = harness.service.confirm_delivery(failed_response.receipt_token)

        # When: the same Gmail truth returns in a later complete generation.
        recovery_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            recovery_generation,
            (gmail,),
            status="complete",
        )
        recovered = harness.service.proactive_check()
        assert recovered.receipt_token is not None
        _ = harness.service.confirm_delivery(recovered.receipt_token)
        repeated = harness.service.proactive_check()
        stored_after_recovery = harness.store.situations.list_situations(limit=10)

    # Then: failure gates only Gmail; recovery offers its preserved row exactly once.
    assert (first_generation.number, failed_generation.number) == (1, 2)
    assert (failed_state.issued, failed_state.applied, failed_state.status) == (
        2,
        2,
        "degraded",
    )
    assert tuple(item.situation_type for item in failed_response.situations) == (
        "calendar_conflict",
    )
    assert failed_response.held_count == 1
    assert failed_response.warnings
    assert failed_response.all_clear is False
    assert {item.situation_type for item in stored_during_failure} == {
        "reply_deadline",
        "calendar_conflict",
    }
    assert tuple(item.situation_type for item in recovered.situations) == (
        "reply_deadline",
    )
    assert recovery_generation.number == 3
    assert repeated.situations == ()
    assert (
        sum(item.situation_type == "reply_deadline" for item in stored_after_recovery)
        == 1
    )


def test_proactive_check_excludes_gmail_during_interrupted_newer_generation(
    tmp_path: Path,
) -> None:
    # Given: complete Gmail truth and an independent Calendar row.
    with open_harness(tmp_path, _NOON, "already_fresh") as harness:
        gmail = Detection(
            situation_type="reply_deadline",
            dedupe_key="interrupted-gmail-row",
            priority="routine",
            title="Fixture reply deadline",
            why_now="Fixture delivery candidate",
            evidence=SituationEvidence(facts={"thread_id": "interrupted-thread"}),
        )
        complete_generation = harness.store.reserve_source_generation("gmail")
        _ = harness.store.situations.apply_source_generation(
            complete_generation,
            (gmail,),
            status="complete",
        )
        _ = harness.store.situations.upsert_detections(
            (pending_detection("interrupted-calendar"),)
        )

        # When: the next generation is reserved but interrupted before acceptance.
        interrupted_generation = harness.store.reserve_source_generation("gmail")
        response = harness.service.proactive_check()
        generation_state = harness.store.source_generation_state("gmail")
        stored = harness.store.situations.list_situations(limit=10)

    # Then: prior accepted Gmail truth cannot cross the in-flight generation.
    assert (complete_generation.number, interrupted_generation.number) == (1, 2)
    assert (
        generation_state.issued,
        generation_state.applied,
        generation_state.status,
    ) == (2, 1, "complete")
    assert tuple(item.situation_type for item in response.situations) == (
        "calendar_conflict",
    )
    assert sum(item.situation_type == "reply_deadline" for item in stored) == 1


def test_proactive_check_warns_when_situation_capacity_rejects_a_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upsert_module, "_MAX_SITUATION_ROWS", 0)
    with open_harness(tmp_path, _BIRTHDAY_MORNING, "already_fresh") as harness:
        harness.store.set_google_auth_state("configured")
        harness.store.record_sync_success("gmail")
        harness.store.record_sync_success("calendar")
        _ = harness.store.remember(birthday_memory())

        response = harness.service.proactive_check()

    assert response.situations == ()
    assert response.held_count == 0
    assert response.all_clear is False
    assert any(
        warning.startswith("situations: persistence capacity rejected 1 detection")
        for warning in response.warnings
    )
