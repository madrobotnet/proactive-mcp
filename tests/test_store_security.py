import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store, UnsafeDatabasePathError
from proactive_mcp.store.private_path import enforce_private_sidecars

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


@pytest.mark.skipif(sys.platform != "linux", reason="Linux strong path defense")
def test_store_rejects_symlinked_database_file(tmp_path: Path) -> None:
    target = tmp_path / "target.db"
    target.touch()
    link = tmp_path / "proactive.db"
    link.symlink_to(target)

    with pytest.raises(UnsafeDatabasePathError), Store(link):
        pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux strong path defense")
def test_store_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "real-state"
    target.mkdir()
    link = tmp_path / "state"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(UnsafeDatabasePathError), Store(link / "proactive.db"):
        pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux strong path defense")
def test_store_rejects_group_writable_ancestor(tmp_path: Path) -> None:
    unsafe_ancestor = tmp_path / "shared"
    unsafe_ancestor.mkdir(mode=0o777)
    unsafe_ancestor.chmod(0o777)

    with (
        pytest.raises(UnsafeDatabasePathError),
        Store(unsafe_ancestor / "state" / "proactive.db"),
    ):
        pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux strong path defense")
def test_parent_swap_cannot_redirect_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    displaced_dir = tmp_path / "displaced"
    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir()
    original_connect = sqlite3.connect

    def swap_parent_before_connect(
        database: str,
        *,
        timeout: float,
    ) -> sqlite3.Connection:
        _ = state_dir.rename(displaced_dir)
        state_dir.symlink_to(attacker_dir, target_is_directory=True)
        return original_connect(database, timeout=timeout)

    monkeypatch.setattr(sqlite3, "connect", swap_parent_before_connect)

    with Store(state_dir / "proactive.db"):
        pass

    assert (displaced_dir / "proactive.db").exists()
    assert not (attacker_dir / "proactive.db").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux sidecar symlink defense")
def test_sidecar_swap_cannot_chmod_symlink_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "proactive.db"
    db_path.touch()
    sidecar = state_dir / "proactive.db-wal"
    sidecar.touch()
    target = tmp_path / "target"
    target.touch(mode=0o644)
    directory_fd = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY)
    original_open = os.open
    swapped = False

    def swap_after_sidecar_open(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )
        if path == sidecar.name and not swapped:
            swapped = True
            sidecar.unlink()
            sidecar.symlink_to(target)
        return descriptor

    monkeypatch.setattr(os, "open", swap_after_sidecar_open)
    try:
        with pytest.raises(UnsafeDatabasePathError):
            enforce_private_sidecars(directory_fd, db_path)
    finally:
        os.close(directory_fd)

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX sidecar mode handling")
def test_private_sidecar_is_not_reopened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "proactive.db"
    db_path.touch(mode=0o600)
    sidecar = state_dir / "proactive.db-shm"
    sidecar.touch(mode=0o600)
    directory_fd = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY)
    original_open = os.open

    def reject_sidecar_reopen(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == sidecar.name:
            error_message = "already-private live sidecar was reopened"
            raise AssertionError(error_message)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", reject_sidecar_reopen)
    try:
        enforce_private_sidecars(directory_fd, db_path)
    finally:
        os.close(directory_fd)

    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform != "linux", reason="Linux descriptor accounting")
def test_failed_store_opens_release_all_file_descriptors(tmp_path: Path) -> None:
    db_path = tmp_path / "proactive.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        _ = connection.execute("CREATE TABLE schema_migrations (invalid INTEGER)")
    descriptor_count = len(tuple(Path("/proc/self/fd").iterdir()))

    for _ in range(20):
        with pytest.raises(sqlite3.OperationalError):
            _ = Store(db_path)

    assert len(tuple(Path("/proc/self/fd").iterdir())) == descriptor_count
