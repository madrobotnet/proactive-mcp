from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from proactive_mcp.clock import Clock
from proactive_mcp.store import MemoryItem, MemoryNotFoundError, NewMemory, Store


class FixedClock:
    _now: datetime

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


def test_remember_returns_typed_item_with_defaults_and_utc_timestamps(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    clock: Clock = FixedClock(now)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        item = store.remember(
            NewMemory(
                kind="person_fact",
                entity="mother",
                content="Mom's birthday",
                date_anchor="--07-18",
                recurrence="yearly",
            )
        )

    assert isinstance(item, MemoryItem)
    assert item.id == 1
    assert item.kind == "person_fact"
    assert item.entity == "mother"
    assert item.content == "Mom's birthday"
    assert item.date_anchor == "--07-18"
    assert item.recurrence == "yearly"
    assert item.lead_days == 7
    assert item.source == "agent_conversation"
    assert item.created_at == now.isoformat()
    assert item.updated_at == now.isoformat()
    assert item.archived is False


def test_recall_preserves_contradictions_and_filters_active_items(
    tmp_path: Path,
) -> None:
    with Store(tmp_path / "proactive.db") as store:
        first = store.remember(
            NewMemory(
                kind="preference",
                entity="Alex",
                content="Alex prefers tea",
                source="manual",
            )
        )
        second = store.remember(
            NewMemory(
                kind="preference",
                entity="Alex",
                content="Alex prefers coffee",
                source="manual",
            )
        )
        archived = store.remember(NewMemory(kind="note", content="Alex's old note"))
        _ = store.forget(archived.id)

        recalled = store.recall("Alex")

    assert recalled == (first, second)


def test_recall_uses_literal_substrings_and_optional_exact_kind(tmp_path: Path) -> None:
    with Store(tmp_path / "proactive.db") as store:
        percent = store.remember(NewMemory(kind="note", content="progress is 100%"))
        underscore = store.remember(
            NewMemory(kind="preference", content="use_name format")
        )
        backslash = store.remember(
            NewMemory(kind="note", content=r"Windows path C:\Temp")
        )
        unrelated = store.remember(NewMemory(kind="commitment", content="unrelated"))

        assert store.recall("%") == (percent,)
        assert store.recall("_") == (underscore,)
        assert store.recall("\\") == (backslash,)
        assert store.recall("", kind="note") == (percent, backslash)
        assert store.recall("") == (percent, underscore, backslash, unrelated)


def test_forget_is_idempotent_and_unknown_id_raises_typed_error(tmp_path: Path) -> None:
    created_at = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    archived_at = datetime(2026, 7, 12, 10, 30, tzinfo=UTC)
    clock = FixedClock(created_at)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        item = store.remember(NewMemory(kind="commitment", content="Call the dentist"))
        clock.set(archived_at)

        forgotten = store.forget(item.id)
        forgotten_again = store.forget(item.id)

        assert store.recall("") == ()
        with pytest.raises(MemoryNotFoundError) as raised:
            _ = store.forget(999)

    assert forgotten.id == item.id
    assert forgotten.archived is True
    assert forgotten.created_at == created_at.isoformat()
    assert forgotten.updated_at == archived_at.isoformat()
    assert forgotten_again == forgotten
    error = raised.value
    assert error.id == 999
    assert str(error) == "memory item 999 not found"
    with pytest.raises(FrozenInstanceError):
        error.__setattr__("id", 1000)
