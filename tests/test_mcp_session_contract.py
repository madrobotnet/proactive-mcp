from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

import anyio
import pytest
from pydantic import BaseModel, ConfigDict

from proactive_mcp.server.situation_responses import (
    ConfirmDeliveryResponse,
    ProactiveCheckResponse,
)
from proactive_mcp.store import Store
from tests.memory_tools_stdio import json_text, memory_session
from tests.situation_tool_support import pending_detection, tool_schema, write_config

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from mcp import ClientSession
    from mcp.types import Tool

_DAILY_TOOLS: Final = frozenset(
    {
        "acknowledge_situation",
        "confirm_delivery",
        "forget",
        "get_situation",
        "get_status",
        "list_entities",
        "list_situations",
        "mute_situation",
        "proactive_check",
        "recall",
        "remember",
        "snooze_situation",
        "update",
    }
)
_SCHEDULED_TOOLS: Final = frozenset(
    {"confirm_delivery", "get_status", "proactive_check"}
)
_SERVE: Final = ("-m", "proactive_mcp", "serve")
_SCHEDULED: Final = ("-m", "proactive_mcp", "serve-scheduled")
_TIMEOUT_SECONDS: Final = 20
_DATABASE_NAME: Final = "memory.db"


@dataclass(frozen=True, slots=True)
class _DeliveryCounts:
    pending: int
    delivered: int
    events: int


class _FieldSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    description: str | None = None


class _ModelSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    properties: dict[str, _FieldSchema]


@asynccontextmanager
async def _session(
    tmp_path: Path,
    server_args: tuple[str, ...],
) -> AsyncGenerator[ClientSession, None]:
    with anyio.fail_after(_TIMEOUT_SECONDS):
        async with memory_session(tmp_path, server_args=server_args) as session:
            yield session


async def _listed_tools(
    tmp_path: Path,
    server_args: tuple[str, ...],
) -> dict[str, Tool]:
    async with _session(tmp_path, server_args) as session:
        listed = await session.list_tools()
    return {tool.name: tool for tool in listed.tools}


def _delivery_counts(tmp_path: Path) -> _DeliveryCounts:
    with Store(tmp_path / _DATABASE_NAME) as store:
        return _DeliveryCounts(
            pending=store.situations.count_situations("pending"),
            delivered=store.situations.count_situations("delivered"),
            events=store.situations.count_deliveries(),
        )


def _seed_critical_conflict(tmp_path: Path) -> None:
    write_config(tmp_path, quiet_hours_start="00:00", quiet_hours_end="00:00")
    with Store(tmp_path / _DATABASE_NAME) as store:
        _ = store.situations.upsert_detections(
            (pending_detection("session-contract", priority="critical"),)
        )


def _folded_routing(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").replace("_", " ").split())


def _require_conditional_confirm_routing(description: str) -> str:
    folded = _folded_routing(description)
    assert "exactly once" in folded
    assert "only when" in folded
    assert "nonempty" in folded or "non empty" in folded
    assert "non null" in folded or "nonnull" in folded
    assert "situations" in folded
    assert "receipt token" in folded
    return folded


@pytest.mark.anyio
async def test_daily_serve_exposes_the_complete_tool_set_when_listed(
    tmp_path: Path,
) -> None:
    # Given: the daily serve profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SERVE)
    # Then: the advertised names equal the complete 13-tool surface.
    assert set(tools) == _DAILY_TOOLS


@pytest.mark.anyio
async def test_scheduled_serve_exposes_only_the_unattended_tool_set_when_listed(
    tmp_path: Path,
) -> None:
    # Given: the scheduled serve profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    # Then: the advertised names equal the three unattended tools.
    assert set(tools) == _SCHEDULED_TOOLS


@pytest.mark.anyio
async def test_proactive_check_input_schema_has_no_required_keys(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    # Then: proactive_check accepts an empty argument object.
    assert tool_schema(tools["proactive_check"]).required == ()


@pytest.mark.anyio
async def test_confirm_delivery_input_schema_requires_receipt_token(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    # Then: confirm_delivery requires exactly receipt_token.
    assert set(tool_schema(tools["confirm_delivery"]).required) == {"receipt_token"}


@pytest.mark.anyio
async def test_empty_scheduled_check_is_tokenless(tmp_path: Path) -> None:
    # Given: an empty scheduled installation.
    async with _session(tmp_path, _SCHEDULED) as session:
        # When: the host calls proactive_check.
        checked = await session.call_tool("proactive_check")

    payload = ProactiveCheckResponse.model_validate_json(json_text(checked))
    # Then: no situations are leased and no receipt is issued.
    assert payload.situations == ()
    assert payload.receipt_token is None
    assert payload.all_clear is False


@pytest.mark.anyio
async def test_scheduled_profile_rejects_remember_without_mutation(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile, which must not expose memory writes.
    before = _delivery_counts(tmp_path)
    async with _session(tmp_path, _SCHEDULED) as session:
        # When: the host calls remember.
        refused = await session.call_tool(
            "remember",
            {"kind": "note", "content": "scheduled-leak"},
        )

    # Then: the call is rejected and delivery history is unchanged.
    assert refused.is_error is True
    assert _delivery_counts(tmp_path) == before


@pytest.mark.anyio
async def test_scheduled_profile_rejects_list_situations_without_mutation(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile, which must not expose general situation reads.
    before = _delivery_counts(tmp_path)
    async with _session(tmp_path, _SCHEDULED) as session:
        # When: the host calls list_situations.
        refused = await session.call_tool("list_situations")

    # Then: the call is rejected and delivery history is unchanged.
    assert refused.is_error is True
    assert _delivery_counts(tmp_path) == before


@pytest.mark.anyio
async def test_missing_receipt_token_confirm_does_not_mutate(tmp_path: Path) -> None:
    # Given: a scheduled installation with no leased receipt.
    before = _delivery_counts(tmp_path)
    async with _session(tmp_path, _SCHEDULED) as session:
        # When: the host calls confirm_delivery without receipt_token.
        refused = await session.call_tool("confirm_delivery", {})

    # Then: the call is rejected and nothing is delivered.
    assert refused.is_error is True
    assert _delivery_counts(tmp_path) == before


@pytest.mark.anyio
async def test_unknown_receipt_token_confirm_does_not_mutate(tmp_path: Path) -> None:
    # Given: a scheduled installation with no matching receipt.
    before = _delivery_counts(tmp_path)
    async with _session(tmp_path, _SCHEDULED) as session:
        # When: the host confirms a non-null unknown token.
        refused = await session.call_tool(
            "confirm_delivery",
            {"receipt_token": "unknown-receipt"},
        )

    # Then: the call is rejected and nothing is delivered.
    assert refused.is_error is True
    assert _delivery_counts(tmp_path) == before


@pytest.mark.anyio
async def test_valid_scheduled_confirm_delivers_the_exact_leased_count(
    tmp_path: Path,
) -> None:
    # Given: one critical local calendar conflict and quiet hours disabled.
    _seed_critical_conflict(tmp_path)
    async with _session(tmp_path, _SCHEDULED) as session:
        checked = await session.call_tool("proactive_check")
        payload = ProactiveCheckResponse.model_validate_json(json_text(checked))
        assert payload.situations
        assert payload.receipt_token is not None
        # When: the host confirms the returned receipt once.
        confirmed = await session.call_tool(
            "confirm_delivery",
            {"receipt_token": payload.receipt_token},
        )

    confirmation = ConfirmDeliveryResponse.model_validate_json(json_text(confirmed))
    counts = _delivery_counts(tmp_path)
    # Then: delivered_count matches the leased set and one event is recorded.
    assert confirmation.delivered_count == len(payload.situations)
    assert confirmation.delivered_count == 1
    assert counts.pending == 0
    assert counts.delivered == 1
    assert counts.events == 1


@pytest.mark.anyio
async def test_replayed_receipt_token_returns_typed_success_without_extra_mutation(
    tmp_path: Path,
) -> None:
    # Given: one already-consumed scheduled receipt.
    _seed_critical_conflict(tmp_path)
    async with _session(tmp_path, _SCHEDULED) as session:
        checked = await session.call_tool("proactive_check")
        payload = ProactiveCheckResponse.model_validate_json(json_text(checked))
        assert payload.receipt_token is not None
        first_result = await session.call_tool(
            "confirm_delivery",
            {"receipt_token": payload.receipt_token},
        )
        # When: the host replays the same token.
        replay_result = await session.call_tool(
            "confirm_delivery",
            {"receipt_token": payload.receipt_token},
        )

    first = ConfirmDeliveryResponse.model_validate_json(json_text(first_result))
    replay = ConfirmDeliveryResponse.model_validate_json(json_text(replay_result))
    # Then: replay succeeds from the immutable result and history stays exact.
    assert (first.status, first.delivered_count) == ("confirmed", 1)
    assert (replay.status, replay.delivered_count) == ("already_confirmed", 1)
    assert _delivery_counts(tmp_path).events == 1


@pytest.mark.anyio
async def test_proactive_check_description_omits_automatic_repeat_rule(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    description = tools["proactive_check"].description or ""
    # Then: the host-facing copy has no automatic long-gap repeat rule.
    assert "long gap" not in description
    assert "again after" not in description


@pytest.mark.anyio
async def test_proactive_check_description_requires_exactly_once(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    # Then: the host is told to check exactly once per new session.
    assert "exactly once" in (tools["proactive_check"].description or "")


@pytest.mark.anyio
async def test_confirm_delivery_description_is_conditional_on_token_and_situations(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    description = tools["confirm_delivery"].description or ""
    # Then: confirmation is gated on nonempty situations and a non-null token.
    _ = _require_conditional_confirm_routing(description)


def test_receipt_token_field_description_is_conditional() -> None:
    # Given: the serialized proactive_check response schema.
    schema = _ModelSchema.model_validate(ProactiveCheckResponse.model_json_schema())
    # When: the host reads the receipt_token field description.
    description = schema.properties["receipt_token"].description
    # Then: confirmation is gated on nonempty situations and a non-null token.
    assert description is not None
    folded = _require_conditional_confirm_routing(description)
    assert "confirm delivery" in folded


@pytest.mark.anyio
async def test_proactive_check_meta_session_contract_is_one_check(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    meta = tools["proactive_check"].meta
    # Then: the machine contract is one_check.
    assert meta is not None
    assert meta["session_contract"] == "one_check"


@pytest.mark.anyio
async def test_confirm_delivery_meta_session_contract_is_conditional_confirm(
    tmp_path: Path,
) -> None:
    # Given: the scheduled profile over stdio.
    # When: the host lists tools.
    tools = await _listed_tools(tmp_path, _SCHEDULED)
    meta = tools["confirm_delivery"].meta
    # Then: the machine contract is conditional_confirm.
    assert meta is not None
    assert meta["session_contract"] == "conditional_confirm"
