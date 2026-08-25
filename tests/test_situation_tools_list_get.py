from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

import proactive_mcp.store._situation_consistency as consistency_module
from proactive_mcp.store import SituationEvidence, SituationNotFoundError
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import (
    UNTRUSTED_SUBJECT,
    open_harness,
    pending_detection,
)

if TYPE_CHECKING:
    from pathlib import Path

_NOON = utc_datetime(2026, 8, 21, 12)


def test_list_and_get_isolate_quoted_external_evidence_as_untrusted(
    tmp_path: Path,
) -> None:
    # Given: one pending situation whose evidence quotes external text.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections((pending_detection("evidence"),))

        # When: the agent lists pending situations and reads one detail.
        listed = harness.service.list_situations("pending")
        delivered = harness.service.list_situations("delivered")
        detail = harness.service.get_situation(listed.items[0].id)

    # Then: quoted external text is exposed as marked untrusted data (§9.4).
    assert delivered.items == ()
    assert listed.items == (detail,)
    assert detail.evidence.facts == {"event_a_id": "evidence"}
    assert detail.evidence.quoted_external.trust == "untrusted_external_data"
    assert detail.evidence.quoted_external.values == {"subject": UNTRUSTED_SUBJECT}
    assert detail.evidence.quoted_memory.trust == "untrusted_memory_data"
    assert detail.evidence.quoted_memory.values == {}


def test_list_situations_is_cursor_paginated_and_stable(tmp_path: Path) -> None:
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections(
            tuple(pending_detection(f"page-{index}") for index in range(25))
        )

        first = harness.service.list_situations(limit=10)
        assert first.next_after_id is not None
        second = harness.service.list_situations(
            after_id=first.next_after_id,
            limit=10,
        )

    first_ids = tuple(item.id for item in first.items)
    second_ids = tuple(item.id for item in second.items)
    assert len(first_ids) == len(second_ids) == 10
    assert set(first_ids).isdisjoint(second_ids)
    assert max(first_ids) < min(second_ids)


def test_situation_row_quota_skips_new_remote_id_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consistency_module, "_MAX_SITUATION_ROWS", 1)
    with open_harness(tmp_path, _NOON) as harness:
        summary = harness.store.situations.upsert_detections(
            (pending_detection("first"), pending_detection("second"))
        )

        count = harness.store.situations.count_situations()

    assert summary.created == 1
    assert summary.skipped == 1
    assert summary.capacity_skipped == 1
    assert count == 1


def test_situation_record_quota_skips_oversized_external_evidence(
    tmp_path: Path,
) -> None:
    oversized = replace(
        pending_detection("oversized"),
        evidence=SituationEvidence(quoted_external={"subject": "x" * 20_000}),
    )
    with open_harness(tmp_path, _NOON) as harness:
        summary = harness.store.situations.upsert_detections((oversized,))

        count = harness.store.situations.count_situations()

    assert summary.created == 0
    assert summary.skipped == 1
    assert summary.capacity_skipped == 1
    assert count == 0


def test_get_situation_rejects_an_unknown_id(tmp_path: Path) -> None:
    # Given: an installation with no situations at all.
    with (
        open_harness(tmp_path, _NOON) as harness,
        # When: the agent asks for an id that was never detected.
        # Then: the tool refuses instead of inventing a situation.
        pytest.raises(SituationNotFoundError),
    ):
        _ = harness.service.get_situation(404)
