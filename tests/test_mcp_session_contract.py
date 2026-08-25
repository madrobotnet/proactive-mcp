from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from proactive_mcp.server.situation_responses import (
    ConfirmDeliveryResponse,
    ProactiveCheckResponse,
)
from tests.mcp_session_contract_support import (
    DAILY_TOOLS as _DAILY_TOOLS,
)
from tests.mcp_session_contract_support import (
    SCHEDULED as _SCHEDULED,
)
from tests.mcp_session_contract_support import (
    SCHEDULED_TOOLS as _SCHEDULED_TOOLS,
)
from tests.mcp_session_contract_support import (
    SERVE as _SERVE,
)
from tests.mcp_session_contract_support import (
    delivery_counts as _delivery_counts,
)
from tests.mcp_session_contract_support import (
    listed_tools as _listed_tools,
)
from tests.mcp_session_contract_support import (
    seed_critical_conflict as _seed_critical_conflict,
)
from tests.mcp_session_contract_support import (
    session as _session,
)
from tests.memory_tools_stdio import json_text
from tests.situation_tool_support import tool_schema

if TYPE_CHECKING:
    from pathlib import Path


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
@pytest.mark.parametrize("server_args", [_SERVE, _SCHEDULED])
@pytest.mark.parametrize("with_situation", [False, True])
async def test_every_proactive_check_exposes_fixed_delivery_protocol_fields(
    tmp_path: Path,
    server_args: tuple[str, ...],
    *,
    with_situation: bool,
) -> None:
    if with_situation:
        _seed_critical_conflict(tmp_path)

    async with _session(tmp_path, server_args) as session:
        checked = await session.call_tool("proactive_check")

    payload = ProactiveCheckResponse.model_validate_json(json_text(checked))
    requires_confirmation = len(payload.situations) > 0 and (
        payload.receipt_token is not None
    )
    assert payload.protocol_version == "1"
    assert payload.confirmation.model_dump() == {"tool": "confirm_delivery"}
    assert payload.requires_confirmation is requires_confirmation
    assert requires_confirmation is with_situation


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
