from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.situations import EngineInputs, SituationEngine
from proactive_mcp.store import NewMemory, Store
from tests.memory_store_support import FixedClock
from tests.memory_tools_stdio import memory_session
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path


def test_free_dated_entity_present(tmp_path: Path) -> None:
    clock = FixedClock(utc_datetime(2026, 8, 21, 9))
    memory = NewMemory(
        kind="note",
        entity="Mom",
        entity_kind="person",
        content="Dentist Friday",
        date_anchor="2026-08-28",
    )

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        first = store.remember(memory)
        duplicate = store.remember(memory)
        dated = store.list_dated_memories()

    assert duplicate.id == first.id
    assert len(dated) == 1
    assert dated[0].entity_id == first.entity_id


def test_free_dated_entity_absent(tmp_path: Path) -> None:
    memory = NewMemory(
        kind="note",
        content="Dentist Friday",
        date_anchor="2026-08-28",
    )

    with Store(tmp_path / "proactive.db") as store:
        first = store.remember(memory)
        duplicate = store.remember(memory)
        dated = store.list_dated_memories()

    assert duplicate.id == first.id
    assert len(dated) == 1
    assert dated[0].entity_id is None


def test_free_dated_kind_distinct(tmp_path: Path) -> None:
    with Store(tmp_path / "proactive.db") as store:
        note = store.remember(
            NewMemory(
                kind="note",
                content="Planning day",
                date_anchor="2026-08-28",
            )
        )
        fact = store.remember(
            NewMemory(
                kind="fact",
                content="Planning day",
                date_anchor="2026-08-28",
            )
        )

    assert note.id != fact.id


def test_free_dated_recurrence_distinct(tmp_path: Path) -> None:
    with Store(tmp_path / "proactive.db") as store:
        one_time = store.remember(
            NewMemory(
                kind="commitment",
                content="Review day",
                date_anchor="2026-08-29",
                recurrence="none",
            )
        )
        yearly = store.remember(
            NewMemory(
                kind="commitment",
                content="Review day",
                date_anchor="2026-08-29",
                recurrence="yearly",
            )
        )

    assert one_time.id != yearly.id


def test_free_dated_normalization_identity(tmp_path: Path) -> None:
    keeper_content = "  Café  Friday  "

    with Store(tmp_path / "proactive.db") as store:
        first = store.remember(
            NewMemory(
                kind="note",
                content=keeper_content,
                date_anchor="2026-08-28",
            )
        )
        duplicate = store.remember(
            NewMemory(
                kind="note",
                content="cafe\u0301 friday",
                date_anchor="2026-08-28",
            )
        )
        punctuation = store.remember(
            NewMemory(
                kind="note",
                content="café friday!",
                date_anchor="2026-08-28",
            )
        )
        whitespace_removed = store.remember(
            NewMemory(
                kind="note",
                content="caféfriday",
                date_anchor="2026-08-28",
            )
        )
        dated = store.list_dated_memories()

    assert duplicate.id == first.id
    assert punctuation.id != first.id
    assert whitespace_removed.id != first.id
    assert len(dated) == 3
    assert dated[0].content == keeper_content


def test_free_dated_ignores_lead_days_and_source(tmp_path: Path) -> None:
    first_content = "dentist friday"

    with Store(tmp_path / "proactive.db") as store:
        first = store.remember(
            NewMemory(
                kind="note",
                content=first_content,
                date_anchor="2026-08-28",
                lead_days=7,
                source="manual",
            )
        )
        duplicate = store.remember(
            NewMemory(
                kind="note",
                content="DENTIST FRIDAY",
                date_anchor="2026-08-28",
                lead_days=3,
                source="agent_conversation",
            )
        )
        dated = store.list_dated_memories()

    assert duplicate.id == first.id
    assert len(dated) == 1
    assert dated[0].content == first_content
    assert dated[0].lead_days == 7
    assert dated[0].source == "manual"


@pytest.mark.anyio
async def test_mixed_mcp_store_free_dated_dedupe(tmp_path: Path) -> None:
    keeper_content = "  Dentist  Friday  "
    async with memory_session(tmp_path) as session:
        _ = await session.call_tool(
            "remember",
            {
                "kind": "note",
                "content": keeper_content,
                "date_anchor": "2026-08-28",
                "recurrence": "none",
                "lead_days": 7,
            },
        )

    clock = FakeClock(utc_datetime(2026, 8, 21, 9))
    with Store(tmp_path / "memory.db", clock=clock) as store:
        _ = store.remember(
            NewMemory(
                kind="note",
                content="dentist friday",
                date_anchor="2026-08-28",
                recurrence="none",
                lead_days=10,
                source="manual",
            )
        )
        engine = SituationEngine(store, clock, UTC)
        _ = engine.evaluate(EngineInputs())
        _ = engine.evaluate(EngineInputs())
        dated = store.list_dated_memories()
        occasions = tuple(
            situation
            for situation in store.situations.list_situations()
            if situation.situation_type == "personal_occasion"
        )

    assert len(occasions) == 1
    assert len(dated) == 1
    assert dated[0].content == keeper_content
    assert dated[0].lead_days == 7
