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
    assert set(entities_schema.properties) >= {"kind", "path_prefix"}

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
