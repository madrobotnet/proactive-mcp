from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.server.situation_tools import SituationToolService
from tests.daemon_test_support import birthday_memory
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import CountingEvaluation as _CountingEvaluation
from tests.situation_tool_support import open_harness, pending_detection

if TYPE_CHECKING:
    from pathlib import Path

_BIRTHDAY_MORNING = utc_datetime(2026, 7, 11, 9)
_NOON = utc_datetime(2026, 8, 21, 12)


def test_proactive_check_delivers_the_detected_occasion_exactly_once(
    tmp_path: Path,
) -> None:
    # Given: a D-7 birthday memory and no prior delivery.
    with open_harness(tmp_path, _BIRTHDAY_MORNING) as harness:
        _ = harness.store.remember(birthday_memory())

        # When: the same session checks twice.
        first = harness.service.proactive_check()
        assert first.receipt_token is not None
        _ = harness.service.confirm_delivery(first.receipt_token)
        second = harness.service.proactive_check()
        stored = harness.store.situations.list_situations()

    # Then: the situation is received once and never re-offered.
    assert tuple(item.situation_type for item in first.situations) == (
        "personal_occasion",
    )
    assert first.situations[0].state == "pending"
    assert first.situations[0].priority == "high"
    assert second.situations == ()
    assert tuple(item.state for item in stored) == ("delivered",)


@pytest.mark.parametrize("with_situation", [False, True])
def test_proactive_check_characterizes_existing_application_payload(
    tmp_path: Path,
    *,
    with_situation: bool,
) -> None:
    with open_harness(tmp_path, _NOON) as harness:
        if with_situation:
            _ = harness.store.situations.upsert_detections(
                (pending_detection("payload-shape"),)
            )

        response = harness.service.proactive_check()

    payload_keys = response.model_dump().keys()
    diagnostic_keys = response.freshness.gmail.diagnostics.model_dump().keys()
    assert bool(response.situations) is with_situation
    assert (response.receipt_token is not None) is with_situation
    assert set(payload_keys) >= {
        "all_clear",
        "budget",
        "freshness",
        "held_count",
        "receipt_token",
        "situations",
        "warnings",
    }
    assert set(diagnostic_keys) == {
        "byte_budget",
        "excluded_count",
        "outcome",
        "page_count",
        "projected_count",
        "reason_counts",
        "request_count",
    }


def test_rapid_proactive_checks_coalesce_expensive_evaluation(tmp_path: Path) -> None:
    with open_harness(tmp_path, _NOON) as harness:
        evaluation = _CountingEvaluation(harness.dependencies.evaluation)
        service = SituationToolService(
            replace(harness.dependencies, evaluation=evaluation)
        )

        _ = service.proactive_check()
        _ = service.proactive_check()

    assert evaluation.calls == 1
