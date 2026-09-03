from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import (
    Entity,
    MemoryItem,
    MemoryRecurrence,
    MemoryValidationError,
    NewMemory,
    Store,
)
from tests.memory_store_support import FixedClock

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.clock import Clock


def test_remember_resolves_entity_from_normalized_path_and_alias(
    tmp_path: Path,
) -> None:
    """Given 엄마 under 가족/어머니, recall 어머니 resolves the same entity."""
    now = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    clock: Clock = FixedClock(now)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        remembered = store.remember(
            NewMemory(
                kind="fact",
                entity="엄마",
                entity_kind="person",
                entity_path=" 가족 / 어머니 ",
                attribute="birthday",
                content="엄마 생일",
                date_anchor="--07-18",
                recurrence="yearly",
            )
        )
        recalled = store.recall("어머니")

    assert isinstance(remembered, MemoryItem)
    assert remembered.entity == "엄마"
    assert remembered.entity_kind == "person"
    assert remembered.entity_path == "가족/어머니"
    assert recalled == (remembered,)


def test_remember_dedupes_dated_facts_and_preserves_birthday_contradictions(
    tmp_path: Path,
) -> None:
    """Given duplicate and conflicting birthdays, only the duplicate merges."""
    created_at = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    updated_at = created_at + timedelta(minutes=1)
    clock = FixedClock(created_at)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        first = store.remember(
            NewMemory(
                kind="fact",
                entity="엄마",
                entity_kind="person",
                attribute="birthday",
                content="엄마 생일은 7월 18일",
                date_anchor="--07-18",
                recurrence="yearly",
            )
        )
        clock.set(updated_at)
        duplicate = store.remember(
            NewMemory(
                kind="fact",
                entity="엄마",
                entity_kind="person",
                attribute="birthday",
                content="엄마 생일은 7월 18일",
                date_anchor="--07-18",
                recurrence="yearly",
            )
        )
        conflicting = store.remember(
            NewMemory(
                kind="fact",
                entity="엄마",
                entity_kind="person",
                attribute="birthday",
                content="엄마 생일은 6월 18일",
                date_anchor="--06-18",
                recurrence="yearly",
            )
        )
        recalled = store.recall("엄마")

    assert duplicate.id == first.id
    assert duplicate.created_at == created_at.isoformat()
    assert duplicate.updated_at == updated_at.isoformat()
    assert tuple(item.id for item in recalled) == (conflicting.id, first.id)
    assert all(item.is_contradictory for item in recalled)


def test_recall_filters_normalized_path_prefix_across_kinds_at_boundaries(
    tmp_path: Path,
) -> None:
    """Given adjacent paths, prefix recall does not cross a segment boundary."""
    with Store(tmp_path / "proactive.db") as store:
        mother = store.remember(
            NewMemory(
                kind="fact",
                entity="엄마",
                entity_kind="person",
                entity_path="가족/어머니",
                content="엄마 메모",
            )
        )
        project = store.remember(
            NewMemory(
                kind="note",
                entity="proactive",
                entity_kind="activity",
                entity_path="가족/프로젝트",
                content="프로젝트 메모",
            )
        )
        similarly_named = store.remember(
            NewMemory(
                kind="note",
                entity="가족력",
                entity_kind="thing",
                entity_path="가족력",
                content="제외되는 메모",
            )
        )

        recalled = store.recall("", path_prefix=" 가족 ")

    assert tuple(item.id for item in recalled) == (project.id, mother.id)
    assert similarly_named not in recalled


def test_recall_defaults_to_twenty_newest_items_and_honors_limit(
    tmp_path: Path,
) -> None:
    """Given more than twenty memories, recall is latest-first with a limit."""
    start = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    clock = FixedClock(start)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        items: list[MemoryItem] = []
        for index in range(21):
            clock.set(start + timedelta(minutes=index))
            items.append(
                store.remember(NewMemory(kind="note", content=f"note {index}"))
            )

        default_recalled = store.recall("")
        limited_recalled = store.recall("", limit=2)

    assert tuple(item.id for item in default_recalled) == tuple(
        item.id for item in reversed(items[1:])
    )
    assert limited_recalled == (items[-1], items[-2])


def test_update_and_list_entities_return_typed_normalized_results(
    tmp_path: Path,
) -> None:
    """Given stored entities, update changes a memory and list filters entities."""
    created_at = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    updated_at = created_at + timedelta(hours=1)
    clock = FixedClock(created_at)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        memory = store.remember(
            NewMemory(
                kind="commitment",
                entity="proactive",
                entity_kind="activity",
                entity_path=" 개발 / proactive-mcp ",
                attribute="deadline",
                content="M2.5 완료",
                date_anchor="2026-08-21",
            )
        )
        _ = store.remember(
            NewMemory(
                kind="note",
                entity="엄마",
                entity_kind="person",
                entity_path="가족/어머니",
                content="엄마 메모",
            )
        )
        clock.set(updated_at)
        changed = store.update(
            memory.id,
            NewMemory(
                kind="commitment",
                entity="proactive",
                entity_kind="activity",
                entity_path="개발/proactive-mcp",
                attribute="deadline",
                content="M2.5 검토",
                date_anchor="2026-08-22",
            ),
        )
        entities = store.list_entities(kind="activity", path_prefix=" 개발 ")

    assert changed.id == memory.id
    assert changed.content == "M2.5 검토"
    assert changed.updated_at == updated_at.isoformat()
    assert memory.entity_id is not None
    assert entities == (
        Entity(
            id=memory.entity_id,
            kind="activity",
            path="개발/proactive-mcp",
            label="proactive",
            status="active",
            created_at=created_at.isoformat(),
            updated_at=created_at.isoformat(),
        ),
    )


@pytest.mark.parametrize(
    ("date_anchor", "recurrence"),
    [
        ("2026-02-30", "none"),
        ("--07-18", "none"),
        (None, "yearly"),
    ],
)
def test_remember_rejects_invalid_date_shapes(
    tmp_path: Path,
    date_anchor: str | None,
    recurrence: MemoryRecurrence,
) -> None:
    with Store(tmp_path / "proactive.db") as store:
        with pytest.raises(MemoryValidationError) as caught:
            _ = store.remember(
                NewMemory(
                    kind="fact",
                    content="Invalid date",
                    date_anchor=date_anchor,
                    recurrence=recurrence,
                )
            )

        assert caught.value.field == "date_anchor"
        assert store.list_dated_memories() == ()


@pytest.mark.parametrize(
    ("date_anchor", "recurrence"),
    [
        ("2026-02-30", "none"),
        ("--07-18", "none"),
        (None, "yearly"),
    ],
)
def test_update_rejects_invalid_date_shapes(
    tmp_path: Path,
    date_anchor: str | None,
    recurrence: MemoryRecurrence,
) -> None:
    with Store(tmp_path / "proactive.db") as store:
        original = store.remember(NewMemory(kind="fact", content="Original"))

        with pytest.raises(MemoryValidationError) as caught:
            _ = store.update(
                original.id,
                NewMemory(
                    kind="fact",
                    content="Changed",
                    date_anchor=date_anchor,
                    recurrence=recurrence,
                ),
            )

        assert caught.value.field == "date_anchor"
        assert store.recall("Original") == (original,)
