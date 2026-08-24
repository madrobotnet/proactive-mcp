from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from proactive_mcp.server import EntityResponse, MemoryItemResponse, RecallResponse
from tests.memory_tools_stdio import json_text, memory_session


class ListEntitiesResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: tuple[EntityResponse, ...]
    next_after_id: int | None = None


@pytest.mark.anyio
async def test_remember_resolves_aliases_normalizes_paths_and_recall_filters(
    tmp_path: Path,
) -> None:
    async with memory_session(tmp_path) as session:
        stored = await session.call_tool(
            "remember",
            {
                "kind": "fact",
                "entity": "엄마",
                "entity_kind": "person",
                "entity_path": " 가족 / 어머니 ",
                "attribute": "birthday",
                "content": "엄마 생신",
                "date_anchor": "--07-18",
                "recurrence": "yearly",
                "lead_days": 3,
            },
        )
        _ = await session.call_tool(
            "remember",
            {
                "kind": "note",
                "entity": "proactive",
                "entity_kind": "activity",
                "entity_path": "가족/프로젝트",
                "content": "프로젝트 메모",
            },
        )
        _ = await session.call_tool(
            "remember",
            {
                "kind": "note",
                "entity": "가족력",
                "entity_kind": "thing",
                "entity_path": "가족력",
                "content": "제외되는 메모",
            },
        )
        by_alias = await session.call_tool("recall", {"query": "어머니"})
        by_path = await session.call_tool(
            "recall",
            {"query": "", "path_prefix": " 가족 "},
        )
        by_entity_kind = await session.call_tool(
            "recall",
            {"query": "", "entity_kind": "person"},
        )
        by_memory_kind = await session.call_tool(
            "recall",
            {"query": "", "kind": "fact"},
        )

    item = MemoryItemResponse.model_validate_json(json_text(stored))
    assert item.trust == "untrusted_memory_data"
    assert item.entity == "엄마"
    assert item.entity_kind == "person"
    assert item.entity_path == "가족/어머니"
    assert item.attribute == "birthday"
    assert item.is_contradictory is False

    alias_payload = RecallResponse.model_validate_json(json_text(by_alias))
    assert tuple(found.id for found in alias_payload.items) == (item.id,)

    path_payload = RecallResponse.model_validate_json(json_text(by_path))
    assert {found.entity_path for found in path_payload.items} == {
        "가족/어머니",
        "가족/프로젝트",
    }

    entity_kind_payload = RecallResponse.model_validate_json(json_text(by_entity_kind))
    assert tuple(found.id for found in entity_kind_payload.items) == (item.id,)

    memory_kind_payload = RecallResponse.model_validate_json(json_text(by_memory_kind))
    assert tuple(found.id for found in memory_kind_payload.items) == (item.id,)


@pytest.mark.anyio
async def test_remember_merges_duplicate_dated_facts_and_exposes_contradictions(
    tmp_path: Path,
) -> None:
    birthday = {
        "kind": "fact",
        "entity": "엄마",
        "entity_kind": "person",
        "attribute": "birthday",
        "content": "엄마 생일",
        "recurrence": "yearly",
    }
    async with memory_session(tmp_path) as session:
        first = await session.call_tool(
            "remember",
            birthday | {"date_anchor": "--07-18"},
        )
        duplicate = await session.call_tool(
            "remember",
            birthday | {"date_anchor": "--07-18"},
        )
        conflicting = await session.call_tool(
            "remember",
            birthday | {"date_anchor": "--06-18"},
        )
        recalled = await session.call_tool("recall", {"query": "엄마"})

    first_item = MemoryItemResponse.model_validate_json(json_text(first))
    duplicate_item = MemoryItemResponse.model_validate_json(json_text(duplicate))
    conflicting_item = MemoryItemResponse.model_validate_json(json_text(conflicting))
    payload = RecallResponse.model_validate_json(json_text(recalled))

    assert duplicate_item.id == first_item.id
    assert {item.id for item in payload.items} == {first_item.id, conflicting_item.id}
    assert all(item.is_contradictory for item in payload.items)


@pytest.mark.anyio
async def test_recall_defaults_to_twenty_newest_items_and_honors_limit(
    tmp_path: Path,
) -> None:
    async with memory_session(tmp_path) as session:
        stored = [
            await session.call_tool(
                "remember",
                {"kind": "note", "content": f"note {index}"},
            )
            for index in range(21)
        ]
        default_recalled = await session.call_tool("recall", {"query": "note"})
        limited_recalled = await session.call_tool(
            "recall",
            {"query": "note", "limit": 2},
        )

    item_ids = [
        MemoryItemResponse.model_validate_json(json_text(result)).id
        for result in stored
    ]
    default_payload = RecallResponse.model_validate_json(json_text(default_recalled))
    limited_payload = RecallResponse.model_validate_json(json_text(limited_recalled))

    assert tuple(item.id for item in default_payload.items) == tuple(
        reversed(item_ids[1:])
    )
    assert tuple(item.id for item in limited_payload.items) == tuple(
        reversed(item_ids[-2:])
    )


@pytest.mark.anyio
async def test_update_and_list_entities_use_v2_fields(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        stored = await session.call_tool(
            "remember",
            {
                "kind": "commitment",
                "entity": "proactive",
                "entity_kind": "activity",
                "entity_path": "개발/proactive-mcp",
                "attribute": "deadline",
                "content": "M2.5 완료",
                "date_anchor": "2026-08-21",
            },
        )
        memory_id = MemoryItemResponse.model_validate_json(json_text(stored)).id
        updated = await session.call_tool(
            "update",
            {
                "id": memory_id,
                "kind": "commitment",
                "entity": "proactive",
                "entity_kind": "activity",
                "entity_path": "개발/proactive-mcp",
                "attribute": "deadline",
                "content": "M2.5 검토",
                "date_anchor": "2026-08-22",
            },
        )
        entities = await session.call_tool(
            "list_entities",
            {"kind": "activity", "path_prefix": " 개발 "},
        )

    changed = MemoryItemResponse.model_validate_json(json_text(updated))
    assert changed.id == memory_id
    assert changed.content == "M2.5 검토"
    assert changed.date_anchor == "2026-08-22"

    listed = ListEntitiesResponse.model_validate_json(json_text(entities))
    assert listed.items[0].trust == "untrusted_memory_data"
    assert [(entity.kind, entity.path, entity.label) for entity in listed.items] == [
        ("activity", "개발/proactive-mcp", "proactive"),
    ]


@pytest.mark.anyio
async def test_list_entities_cursor_reaches_later_sorted_entities(
    tmp_path: Path,
) -> None:
    async with memory_session(tmp_path) as session:
        for label in ("Zulu", "Alpha", "Mike"):
            _ = await session.call_tool(
                "remember",
                {
                    "kind": "note",
                    "entity": label,
                    "entity_kind": "thing",
                    "content": f"{label} note",
                },
            )
        first_result = await session.call_tool("list_entities", {"limit": 2})
        first = ListEntitiesResponse.model_validate_json(json_text(first_result))
        assert first.next_after_id is not None
        second_result = await session.call_tool(
            "list_entities",
            {"after_id": first.next_after_id, "limit": 2},
        )

    second = ListEntitiesResponse.model_validate_json(json_text(second_result))
    assert tuple(entity.label for entity in first.items) == ("Alpha", "Mike")
    assert tuple(entity.label for entity in second.items) == ("Zulu",)
    assert {entity.id for entity in first.items}.isdisjoint(
        entity.id for entity in second.items
    )
    assert second.next_after_id is None
