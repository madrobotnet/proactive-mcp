import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict

from proactive_mcp.server import ForgetResponse, MemoryItemResponse, RecallResponse


class ToolInputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    properties: dict[str, "ToolInputProperty"]
    required: tuple[str, ...] = ()


class ToolInputProperty(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")


@asynccontextmanager
async def memory_session(tmp_path: Path) -> AsyncGenerator[ClientSession, None]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proactive_mcp.server"],
        env={"PROACTIVE_DATABASE": str(tmp_path / "memory.db")},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        _ = await session.initialize()
        yield session


def _json_text(result: CallToolResult) -> str:
    assert result.is_error is False
    assert result.content
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


@pytest.mark.anyio
async def test_memory_tool_schemas(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        listed = await session.list_tools()

    tools = {tool.name: tool for tool in listed.tools}
    assert {"get_status", "remember", "recall", "forget"} <= set(tools)

    remember = tools["remember"]
    assert remember.description is not None
    description = remember.description.lower()
    assert "date" in description
    assert "appointment" in description
    assert "preference" in description
    assert "people" in description
    assert "date_anchor" in description
    assert "yearly" in description

    remember_schema = ToolInputSchema.model_validate(remember.input_schema)
    assert set(remember_schema.required) >= {"kind", "content"}
    assert set(remember_schema.properties) >= {
        "kind",
        "content",
        "entity",
        "date_anchor",
        "recurrence",
        "lead_days",
    }

    recall_schema = ToolInputSchema.model_validate(tools["recall"].input_schema)
    assert set(recall_schema.required) >= {"query"}
    assert set(recall_schema.properties) >= {"query", "kind"}

    forget_schema = ToolInputSchema.model_validate(tools["forget"].input_schema)
    assert set(forget_schema.required) >= {"id"}
    assert "id" in forget_schema.properties


@pytest.mark.anyio
async def test_remember_then_recall_mothers_birthday(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        stored = await session.call_tool(
            "remember",
            {
                "kind": "person_fact",
                "entity": "mother",
                "content": "엄마 생신",
                "date_anchor": "--07-18",
                "recurrence": "yearly",
                "lead_days": 3,
            },
        )
        item = MemoryItemResponse.model_validate_json(_json_text(stored))
        recalled = await session.call_tool("recall", {"query": "엄마"})

    assert item.kind == "person_fact"
    assert item.entity == "mother"
    assert item.content == "엄마 생신"
    assert item.date_anchor == "--07-18"
    assert item.recurrence == "yearly"
    assert item.lead_days == 3
    assert item.source == "agent_conversation"
    assert item.archived is False
    assert item.id > 0

    payload = RecallResponse.model_validate_json(_json_text(recalled))
    assert len(payload.items) == 1
    found = payload.items[0]
    assert found.id == item.id
    assert found.content == "엄마 생신"
    assert found.date_anchor == "--07-18"
    assert found.recurrence == "yearly"
    assert found.lead_days == 3


@pytest.mark.anyio
async def test_recall_preserves_contradictions(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        first = await session.call_tool(
            "remember",
            {
                "kind": "preference",
                "entity": "Alex",
                "content": "Alex prefers tea",
            },
        )
        second = await session.call_tool(
            "remember",
            {
                "kind": "preference",
                "entity": "Alex",
                "content": "Alex prefers coffee",
            },
        )
        recalled = await session.call_tool("recall", {"query": "Alex"})

    first_id = MemoryItemResponse.model_validate_json(_json_text(first)).id
    second_id = MemoryItemResponse.model_validate_json(_json_text(second)).id
    payload = RecallResponse.model_validate_json(_json_text(recalled))
    contents = {item.content for item in payload.items}
    ids = {item.id for item in payload.items}
    assert contents == {"Alex prefers tea", "Alex prefers coffee"}
    assert ids == {first_id, second_id}


@pytest.mark.anyio
async def test_recall_filters_by_kind(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        preference = await session.call_tool(
            "remember",
            {
                "kind": "preference",
                "entity": "Alex",
                "content": "Alex prefers tea",
            },
        )
        _ = await session.call_tool(
            "remember",
            {
                "kind": "note",
                "entity": "Alex",
                "content": "Alex leftover note",
            },
        )
        recalled = await session.call_tool(
            "recall",
            {"query": "Alex", "kind": "preference"},
        )

    payload = RecallResponse.model_validate_json(_json_text(recalled))
    assert {item.kind for item in payload.items} == {"preference"}
    assert {item.id for item in payload.items} == {
        MemoryItemResponse.model_validate_json(_json_text(preference)).id
    }


@pytest.mark.anyio
async def test_forget_archives_and_unknown_id_is_tool_error(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        stored = await session.call_tool(
            "remember",
            {
                "kind": "commitment",
                "content": "Call the dentist",
            },
        )
        memory_id = MemoryItemResponse.model_validate_json(_json_text(stored)).id

        forgotten = await session.call_tool("forget", {"id": memory_id})
        again = await session.call_tool("forget", {"id": memory_id})
        recalled = await session.call_tool("recall", {"query": "dentist"})
        missing = await session.call_tool("forget", {"id": 999})

    forgotten_payload = ForgetResponse.model_validate_json(_json_text(forgotten))
    assert forgotten_payload.id == memory_id
    assert forgotten_payload.archived is True

    assert ForgetResponse.model_validate_json(_json_text(again)).archived is True
    assert RecallResponse.model_validate_json(_json_text(recalled)).items == ()
    assert missing.is_error is True


@pytest.mark.anyio
async def test_remember_rejects_invalid_date_anchor(tmp_path: Path) -> None:
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

    assert invalid.is_error is True
    assert private_marker not in " ".join(
        content.text for content in invalid.content if isinstance(content, TextContent)
    )


@pytest.mark.anyio
async def test_remember_rejects_yearless_nonrecurring_date(tmp_path: Path) -> None:
    async with memory_session(tmp_path) as session:
        invalid = await session.call_tool(
            "remember",
            {
                "kind": "person_fact",
                "content": "Birthday",
                "date_anchor": "--07-18",
                "recurrence": "none",
            },
        )

    assert invalid.is_error is True
