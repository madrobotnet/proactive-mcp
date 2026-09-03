from pathlib import Path
from typing import ClassVar

import pytest
from mcp.types import TextContent
from pydantic import BaseModel, ConfigDict

from proactive_mcp.server import ForgetResponse, MemoryItemResponse, RecallResponse
from tests.memory_tools_stdio import json_text, memory_session


class ToolInputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    properties: dict[str, "ToolInputProperty"]
    required: tuple[str, ...] = ()


class ToolInputProperty(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")


@pytest.mark.anyio
async def test_memory_tool_schemas_expose_the_v2_contract(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        listed = await session.list_tools()

    tools = {tool.name: tool for tool in listed.tools}
    assert {
        "get_status",
        "remember",
        "recall",
        "update",
        "list_entities",
        "forget",
    } <= set(tools)
    assert "untrusted" in (tools["proactive_check"].description or "")
    assert "untrusted_memory_data" in (tools["recall"].description or "")

    remember_schema = ToolInputSchema.model_validate(tools["remember"].input_schema)
    assert set(remember_schema.required) >= {"kind", "content"}
    assert set(remember_schema.properties) >= {
        "kind",
        "content",
        "entity",
        "entity_kind",
        "entity_path",
        "attribute",
        "date_anchor",
        "recurrence",
        "lead_days",
    }

    recall_schema = ToolInputSchema.model_validate(tools["recall"].input_schema)
    assert set(recall_schema.required) >= {"query"}
    assert set(recall_schema.properties) >= {
        "query",
        "kind",
        "entity_kind",
        "path_prefix",
        "limit",
    }

    update_schema = ToolInputSchema.model_validate(tools["update"].input_schema)
    assert set(update_schema.required) >= {"id", "kind", "content"}
    assert set(update_schema.properties) >= {
        "id",
        "kind",
        "content",
        "entity",
        "entity_kind",
        "entity_path",
        "attribute",
        "date_anchor",
        "recurrence",
        "lead_days",
    }

    entities_schema = ToolInputSchema.model_validate(
        tools["list_entities"].input_schema
    )
    assert set(entities_schema.properties) >= {
        "kind",
        "path_prefix",
        "after_id",
        "limit",
    }

    forget_schema = ToolInputSchema.model_validate(tools["forget"].input_schema)
    assert set(forget_schema.required) >= {"id"}
    assert "id" in forget_schema.properties


@pytest.mark.anyio
async def test_forget_remains_a_soft_archive(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        stored = await session.call_tool(
            "remember",
            {"kind": "commitment", "content": "Call the dentist"},
        )
        memory_id = MemoryItemResponse.model_validate_json(json_text(stored)).id
        forgotten = await session.call_tool("forget", {"id": memory_id})
        again = await session.call_tool("forget", {"id": memory_id})
        recalled = await session.call_tool("recall", {"query": "dentist"})

    forgotten_payload = ForgetResponse.model_validate_json(json_text(forgotten))
    assert forgotten_payload.id == memory_id
    assert forgotten_payload.archived is True
    assert ForgetResponse.model_validate_json(json_text(again)).archived is True
    assert RecallResponse.model_validate_json(json_text(recalled)).items == ()


@pytest.mark.anyio
async def test_remember_rejects_invalid_dates_without_reflecting_input(
    tmp_path: Path,
) -> None:
    private_marker = "PRIVATE-DATE-MARKER"
    async with memory_session(tmp_path) as session:
        invalid = await session.call_tool(
            "remember",
            {
                "kind": "commitment",
                "content": "Call next week",
                "date_anchor": private_marker,
            },
        )
        yearless_nonrecurring = await session.call_tool(
            "remember",
            {
                "kind": "fact",
                "content": "Birthday",
                "date_anchor": "--07-18",
                "recurrence": "none",
            },
        )

    assert invalid.is_error is True
    assert private_marker not in " ".join(
        content.text for content in invalid.content if isinstance(content, TextContent)
    )
    assert yearless_nonrecurring.is_error is True


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name", ["remember", "update"])
@pytest.mark.parametrize(
    ("date_anchor", "recurrence"),
    [
        ("2026-02-30", "none"),
        ("--07-18", "none"),
        (None, "yearly"),
    ],
)
async def test_memory_writes_reject_invalid_date_shapes(
    tmp_path: Path,
    tool_name: str,
    date_anchor: str | None,
    recurrence: str,
) -> None:
    async with memory_session(tmp_path) as session:
        arguments = {
            "kind": "fact",
            "content": "Invalid date",
            "date_anchor": date_anchor,
            "recurrence": recurrence,
        }
        if tool_name == "update":
            stored = await session.call_tool(
                "remember",
                {"kind": "fact", "content": "Original"},
            )
            result = await session.call_tool(
                "update",
                {
                    "id": MemoryItemResponse.model_validate_json(json_text(stored)).id,
                    **arguments,
                },
            )
        else:
            result = await session.call_tool("remember", arguments)

    assert result.is_error is True


@pytest.mark.anyio
async def test_memory_tools_enforce_storage_and_result_bounds(tmp_path: Path) -> None:
    exact_content = "x" * 4096
    oversized_utf8 = "한" * 1366
    async with memory_session(tmp_path) as session:
        accepted = await session.call_tool(
            "remember",
            {"kind": "note", "content": exact_content},
        )
        oversized = await session.call_tool(
            "remember",
            {"kind": "note", "content": oversized_utf8},
        )
        excessive_lead = await session.call_tool(
            "remember",
            {"kind": "note", "content": "bounded", "lead_days": 367},
        )
        boundary_date = await session.call_tool(
            "remember",
            {
                "kind": "commitment",
                "content": "bounded",
                "date_anchor": "9999-12-31",
            },
        )
        recall_overflow = await session.call_tool(
            "recall",
            {"query": "", "limit": 101},
        )
        entity_overflow = await session.call_tool(
            "list_entities",
            {"limit": 101},
        )

    assert accepted.is_error is False
    assert oversized.is_error is True
    assert excessive_lead.is_error is True
    assert boundary_date.is_error is True
    assert recall_overflow.is_error is True
    assert entity_overflow.is_error is True
