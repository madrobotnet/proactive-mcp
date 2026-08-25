from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from proactive_mcp.server.situation_responses import ProactiveCheckResponse
from tests.mcp_session_contract_support import (
    SCHEDULED as _SCHEDULED,
)
from tests.mcp_session_contract_support import (
    listed_tools as _listed_tools,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FieldSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    description: str | None = None


class _ModelSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    properties: dict[str, _FieldSchema]


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
