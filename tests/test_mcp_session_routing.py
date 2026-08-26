from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.mcp_session_contract_support import SCHEDULED as _SCHEDULED
from tests.mcp_session_contract_support import listed_tools as _listed_tools

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.anyio
async def test_proactive_check_meta_session_contract_is_one_check(
    tmp_path: Path,
) -> None:
    tools = await _listed_tools(tmp_path, _SCHEDULED)

    meta = tools["proactive_check"].meta

    assert meta is not None
    assert meta["session_contract"] == "one_check"


@pytest.mark.anyio
async def test_confirm_delivery_meta_session_contract_is_conditional_confirm(
    tmp_path: Path,
) -> None:
    tools = await _listed_tools(tmp_path, _SCHEDULED)

    meta = tools["confirm_delivery"].meta

    assert meta is not None
    assert meta["session_contract"] == "conditional_confirm"
