from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from multiprocessing import get_context
from typing import TYPE_CHECKING
from unicodedata import normalize

import pytest

import proactive_mcp.store.memory as memory_module
from proactive_mcp.store import (
    MemoryNotFoundError,
    MemoryValidationError,
    NewMemory,
    Store,
)
from tests.memory_store_support import FixedClock

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as ProcessQueue
    from multiprocessing.synchronize import Barrier as ProcessBarrier
    from pathlib import Path


def test_empty_path_prefix_is_rejected(tmp_path: Path) -> None:
    """Given an explicit empty prefix, recall and entity listing reject it."""
    with Store(tmp_path / "proactive.db") as store:
        with pytest.raises(MemoryValidationError):
            _ = store.recall("", path_prefix="")
        with pytest.raises(MemoryValidationError):
            _ = store.list_entities(path_prefix="")


@pytest.mark.parametrize(
    "path",
    [
        "가족//어머니",
        "/가족/어머니",
        "가족/어머니/생일/올해",
    ],
)
def test_remember_rejects_empty_and_overdeep_path_segments(
    tmp_path: Path,
    path: str,
) -> None:
    """Given invalid path boundaries, remember raises a typed validation error."""
    with (
        Store(tmp_path / "proactive.db") as store,
        pytest.raises(MemoryValidationError),
    ):
        _ = store.remember(
            NewMemory(
                kind="note",
                entity="엄마",
                entity_kind="person",
                entity_path=path,
                content="엄마 메모",
            )
        )


def test_remember_rejects_explicit_empty_entity_path(tmp_path: Path) -> None:
    """Given an explicit empty path, remember rejects it instead of clearing it."""
    with (
        Store(tmp_path / "proactive.db") as store,
        pytest.raises(MemoryValidationError),
    ):
        _ = store.remember(
            NewMemory(
                kind="note",
                entity="엄마",
                entity_kind="person",
                entity_path="",
                content="엄마 메모",
            )
        )


def test_remember_normalizes_unicode_nfc_paths(tmp_path: Path) -> None:
    """Given decomposed Unicode, stored entity paths use NFC."""
    decomposed = "가족/어머니"

    with Store(tmp_path / "proactive.db") as store:
        item = store.remember(
            NewMemory(
                kind="note",
                entity="엄마",
                entity_kind="person",
                entity_path=decomposed,
                content="엄마 메모",
            )
        )

    assert item.entity_path == normalize("NFC", decomposed)


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


def test_remember_rolls_back_entity_and_alias_when_memory_insert_fails(
    tmp_path: Path,
) -> None:
    """Given a forced insert failure, no orphan entity or alias remains."""
    with Store(tmp_path / "proactive.db") as store:
        connection = store.connection()
        _ = connection.execute(
            """
            CREATE TRIGGER reject_memory_insert
            BEFORE INSERT ON memory_items
            BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = store.remember(
                NewMemory(
                    kind="note",
                    entity="엄마",
                    entity_kind="person",
                    content="엄마 메모",
                )
            )

        assert _scalar_count(connection, "SELECT COUNT(*) FROM entities") == 0
        assert _scalar_count(connection, "SELECT COUNT(*) FROM entity_aliases") == 0
        assert _scalar_count(connection, "SELECT COUNT(*) FROM memory_items") == 0


def test_memory_row_quota_rejects_growth_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_module, "_MAX_MEMORY_ROWS", 1)
    with Store(tmp_path / "proactive.db") as store:
        _ = store.remember(NewMemory(kind="note", content="first"))
        connection = store.connection()

        with pytest.raises(MemoryValidationError):
            _ = store.remember(NewMemory(kind="note", content="second"))

        assert _scalar_count(connection, "SELECT COUNT(*) FROM memory_items") == 1
        assert store.recall("")[0].content == "first"


def test_memory_quota_counts_only_active_rows_after_forget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_module, "_MAX_MEMORY_ROWS", 2)
    with Store(tmp_path / "proactive.db") as store:
        first = store.remember(NewMemory(kind="note", content="first"))
        _ = store.remember(NewMemory(kind="note", content="second"))

        with pytest.raises(MemoryValidationError):
            _ = store.remember(NewMemory(kind="note", content="over limit"))

        forgotten = store.forget(first.id)
        replacement = store.remember(NewMemory(kind="note", content="replacement"))
        connection = store.connection()

        assert forgotten.archived is True
        assert replacement.content == "replacement"
        assert (
            _scalar_count(
                connection,
                "SELECT COUNT(*) FROM memory_items WHERE archived = 0",
            )
            == 2
        )
        assert (
            _scalar_count(
                connection,
                "SELECT COUNT(*) FROM memory_items WHERE archived = 1",
            )
            == 1
        )
        assert _scalar_count(connection, "SELECT COUNT(*) FROM memory_items") == 3

        with pytest.raises(MemoryValidationError):
            _ = store.remember(NewMemory(kind="note", content="over limit again"))


def test_migration_archived_rows_do_not_consume_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_module, "_MAX_MEMORY_ROWS", 1)
    with Store(tmp_path / "proactive.db") as store:
        loser = store.remember(NewMemory(kind="note", content="migration loser"))
        connection = store.connection()
        _ = connection.execute(
            "UPDATE memory_items SET archived = 1 WHERE id = ?",
            (loser.id,),
        )
        connection.commit()

        replacement = store.remember(NewMemory(kind="note", content="replacement"))

        assert replacement.archived is False
        assert (
            _scalar_count(
                connection,
                "SELECT COUNT(*) FROM memory_items WHERE archived = 0",
            )
            == 1
        )
        assert (
            _scalar_count(
                connection,
                "SELECT COUNT(*) FROM memory_items WHERE archived = 1",
            )
            == 1
        )
        assert _scalar_count(connection, "SELECT COUNT(*) FROM memory_items") == 2


def test_update_rolls_back_new_entity_when_memory_update_fails(
    tmp_path: Path,
) -> None:
    """Given a forced update failure, a newly created entity is rolled back."""
    with Store(tmp_path / "proactive.db") as store:
        original = store.remember(NewMemory(kind="note", content="original note"))
        connection = store.connection()
        _ = connection.execute(
            """
            CREATE TRIGGER reject_memory_update
            BEFORE UPDATE ON memory_items
            BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = store.update(
                original.id,
                NewMemory(
                    kind="note",
                    entity="엄마",
                    entity_kind="person",
                    content="updated note",
                ),
            )

        assert _scalar_count(connection, "SELECT COUNT(*) FROM entities") == 0
        assert _scalar_count(connection, "SELECT COUNT(*) FROM entity_aliases") == 0
        assert _scalar_count(connection, "SELECT COUNT(*) FROM memory_items") == 1


def test_commit_failure_rolls_back_and_closes_write_transaction(
    tmp_path: Path,
) -> None:
    """Given a deferred FK violation, failed commit leaves no active transaction."""
    with Store(tmp_path / "proactive.db") as store:
        connection = store.connection()
        _ = connection.execute(
            """
            CREATE TRIGGER violate_fk_after_memory_insert
            AFTER INSERT ON memory_items
            BEGIN
                INSERT INTO entity_aliases (
                    entity_id, alias, alias_norm, source, created_at
                ) VALUES (999, 'invalid', 'invalid', 'manual', '2026-08-21');
            END
            """
        )
        _ = connection.execute("PRAGMA defer_foreign_keys = ON")

        with pytest.raises(sqlite3.IntegrityError):
            _ = store.remember(NewMemory(kind="note", content="rollback probe"))

        assert connection.in_transaction is False
        assert _scalar_count(connection, "SELECT COUNT(*) FROM memory_items") == 0
        assert _scalar_count(connection, "SELECT COUNT(*) FROM entity_aliases") == 0


def _remember_identical_at_barrier(
    db_path: Path,
    barrier: ProcessBarrier,
    results: ProcessQueue[tuple[str, int]],
) -> None:
    assert barrier.wait(timeout=30) >= 0
    with Store(db_path) as store:
        item = store.remember(
            NewMemory(
                kind="fact",
                entity="Ada Lovelace",
                entity_kind="person",
                attribute="birthday",
                content="Ada birthday",
                date_anchor="--07-18",
                recurrence="yearly",
            )
        )
    results.put(("remembered", item.id))


def test_concurrent_identical_remembers_return_one_stable_id(
    tmp_path: Path,
) -> None:
    """Given two simultaneous identical remembers, one stable id wins."""
    db_path = tmp_path / "proactive.db"
    process_count = 2
    context = get_context("spawn")
    barrier = context.Barrier(process_count)
    results: ProcessQueue[tuple[str, int]] = context.Queue()
    processes = [
        context.Process(
            target=_remember_identical_at_barrier,
            args=(db_path, barrier, results),
        )
        for _ in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)

    assert [(process.is_alive(), process.exitcode) for process in processes] == [
        (False, 0)
    ] * process_count
    observed = [results.get(timeout=10) for _ in range(process_count)]
    assert [status for status, _ in observed] == ["remembered"] * process_count
    ids = {memory_id for _, memory_id in observed}
    assert len(ids) == 1
    remembered_id = next(iter(ids))

    with Store(db_path) as store:
        recalled = store.recall("Ada")

    assert tuple(item.id for item in recalled) == (remembered_id,)


def _scalar_count(connection: sqlite3.Connection, select_sql: str) -> int:
    values: list[int] = []

    def capture(value: int) -> int:
        values.append(value)
        return 0

    connection.create_function("_test_count_capture", 1, capture)
    _ = connection.execute(f"SELECT SUM(_test_count_capture(({select_sql})))")
    return values[0] if values else 0
