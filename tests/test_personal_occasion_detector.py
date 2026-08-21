from __future__ import annotations

from dataclasses import replace
from datetime import UTC

from proactive_mcp import situations
from proactive_mcp.store import MemoryItem
from tests.situation_test_support import require_m3, utc_datetime


def _memory(
    memory_id: int,
    date_anchor: str,
    *,
    entity_id: int | None = 4,
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        kind="fact",
        entity_id=entity_id,
        entity="Mother" if entity_id is not None else None,
        entity_kind="person" if entity_id is not None else None,
        entity_path="family/mother" if entity_id is not None else None,
        attribute="birthday",
        content="Fixture birthday",
        date_anchor=date_anchor,
        recurrence="yearly" if date_anchor.startswith("--") else "none",
        lead_days=7,
        source="manual",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        archived=False,
        is_contradictory=False,
    )


def test_personal_occasion_detects_yearly_entity_at_lead_day() -> None:
    # Given: an active yearly birthday exactly seven local dates away.
    require_m3("detect_personal_occasions")
    memory = _memory(11, "--07-18")

    # When: the detector runs at D-7.
    detected = situations.detect_personal_occasions(
        items=(memory,),
        now=utc_datetime(2026, 7, 11, 9),
        tz=UTC,
    )

    # Then: one high-priority occurrence is keyed by entity, attribute, and year.
    assert len(detected) == 1
    candidate = detected[0]
    assert candidate.situation_type == "personal_occasion"
    assert candidate.priority == "high"
    assert candidate.dedupe_key == "personal_occasion:entity:4:birthday:2026"
    assert candidate.evidence.facts["days_until"] == "7"
    assert candidate.evidence.facts["memory_ids"] == "11"


def test_personal_occasion_ignores_item_before_lead_window() -> None:
    # Given: an active occasion eight days away with a seven-day lead.
    require_m3("detect_personal_occasions")

    # When: the detector evaluates before D-7.
    detected = situations.detect_personal_occasions(
        items=(_memory(1, "--07-18"),),
        now=utc_datetime(2026, 7, 10, 9),
        tz=UTC,
    )

    # Then: no premature detection is produced.
    assert detected == ()


def test_personal_occasion_collapses_contradictory_entity_dates_with_evidence() -> None:
    # Given: two active birthday dates for one entity, both inside their lead window.
    require_m3("detect_personal_occasions")
    first = _memory(31, "--07-18")
    second = replace(
        first,
        id=32,
        date_anchor="--07-19",
        lead_days=8,
        is_contradictory=True,
    )

    # When: both contradictory dates are evaluated.
    detected = situations.detect_personal_occasions(
        items=(first, second),
        now=utc_datetime(2026, 7, 11, 9),
        tz=UTC,
    )

    # Then: one annual candidate carries both structured dates and memory ids.
    assert len(detected) == 1
    candidate = detected[0]
    assert candidate.dedupe_key == "personal_occasion:entity:4:birthday:2026"
    assert candidate.evidence.contradictory_dates == ("--07-18", "--07-19")
    assert candidate.evidence.facts["memory_ids"] == "31,32"


def test_personal_occasion_uses_memory_identity_without_entity() -> None:
    # Given: two entity-free dated memories.
    require_m3("detect_personal_occasions")
    memories = tuple(
        _memory(memory_id, "2026-08-28", entity_id=None) for memory_id in (41, 42)
    )

    # When: both reach D-7.
    detected = situations.detect_personal_occasions(
        items=memories,
        now=utc_datetime(2026, 8, 21, 9),
        tz=UTC,
    )

    # Then: entity-free memories remain independently deduplicated.
    assert {candidate.dedupe_key for candidate in detected} == {
        "personal_occasion:item:41:2026",
        "personal_occasion:item:42:2026",
    }


def test_personal_occasion_creates_new_identity_in_following_year() -> None:
    # Given: one yearly occasion evaluated in consecutive years.
    require_m3("detect_personal_occasions")
    memory = _memory(11, "--07-18")

    # When: D-7 is evaluated for both occurrences.
    first = situations.detect_personal_occasions(
        items=(memory,),
        now=utc_datetime(2026, 7, 11, 9),
        tz=UTC,
    )
    second = situations.detect_personal_occasions(
        items=(memory,),
        now=utc_datetime(2027, 7, 11, 9),
        tz=UTC,
    )

    # Then: recurrence produces a distinct annual key.
    assert first[0].dedupe_key.endswith(":2026")
    assert second[0].dedupe_key.endswith(":2027")
