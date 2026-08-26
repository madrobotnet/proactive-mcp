import os
import sqlite3
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store, UnsafeDatabasePathError

if TYPE_CHECKING or os.name == "nt":
    from proactive_mcp.store import windows_path


def test_store_connection_enforces_foreign_keys(tmp_path: Path) -> None:
    """Given a Store connection, a dangling entity reference is rejected."""
    created = "2026-08-21T00:00:00+00:00"

    with Store(tmp_path / "proactive.db") as store:
        connection = store.connection()
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO memory_items (
                    kind, entity_id, content, source, created_at, updated_at
                ) VALUES ('note', 999, 'dangling entity', 'manual', ?, ?)
                """,
                (created, created),
            )


def test_macos_platform_creates_private_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "platform", "darwin")
        with Store(tmp_path / "proactive.db") as store:
            status = store.status()

    assert status.path == (tmp_path / "proactive.db").absolute()
    assert status.journal_mode.lower() == "wal"
    assert status.migration_version == 10


@pytest.mark.skipif(os.name == "nt", reason="Windows permissions use ACLs")
def test_store_enforces_private_state_permissions(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    db_path = state_dir / "proactive.db"
    previous_umask = os.umask(0o002)
    try:
        with Store(db_path):
            assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
            assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
            init_lock = state_dir / ".proactive.db.init.lock"
            assert stat.S_IMODE(init_lock.stat().st_mode) == 0o600
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_name(f"{db_path.name}{suffix}")
                if sidecar.exists():
                    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    finally:
        _ = os.umask(previous_umask)


@pytest.mark.skipif(os.name == "nt", reason="POSIX create-race behavior")
def test_store_retries_transient_private_file_create_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = os.open
    create_attempts = 0

    def open_with_transient_create_race(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal create_attempts
        if path == "proactive.db" and flags & os.O_CREAT:
            create_attempts += 1
            if create_attempts == 1:
                raise FileNotFoundError(2, "transient create race", path)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", open_with_transient_create_race)

    with Store(tmp_path / "proactive.db"):
        pass

    assert create_attempts == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL assertion")
def test_windows_store_installs_protected_current_user_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state" / "proactive.db"
    applied: list[int] = []
    original_set_private_dacl = windows_path.set_private_dacl

    def record_private_dacl(
        handle: int,
        path: Path,
        *,
        inheritance: int,
    ) -> None:
        applied.append(inheritance)
        original_set_private_dacl(handle, path, inheritance=inheritance)

    monkeypatch.setattr(windows_path, "set_private_dacl", record_private_dacl)

    with Store(db_path):
        pass

    assert applied
    assert 0 in applied
    assert 3 in applied


@pytest.mark.skipif(os.name != "nt", reason="Windows identity pinning")
def test_windows_store_pins_parent_and_database_for_its_lifetime(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    db_path = state / "proactive.db"

    with Store(db_path):
        with pytest.raises(PermissionError):
            db_path.unlink()
        with pytest.raises(PermissionError):
            _ = state.rename(tmp_path / "replaced-state")


@pytest.mark.skipif(os.name != "nt", reason="Windows hard-link defense")
def test_windows_store_rejects_hardlinked_database(tmp_path: Path) -> None:
    state = tmp_path / "state"
    original = state / "original.db"
    db_path = state / "proactive.db"
    with Store(original):
        pass
    os.link(original, db_path)

    with pytest.raises(UnsafeDatabasePathError), Store(db_path):
        pass
