from __future__ import annotations

import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path

import pytest

from proactive_mcp.store import Store, UnsafeDatabasePathError
from proactive_mcp.store.private_path import enforce_private_sidecars


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
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX sidecar identity validation")
def test_private_sidecar_is_reopened_for_identity_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "proactive.db"
    db_path.touch(mode=0o600)
    sidecar = tmp_path / "proactive.db-shm"
    sidecar.touch(mode=0o600)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_open = os.open
    reopened = False

    def record_sidecar_reopen(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal reopened
        if path == sidecar.name:
            reopened = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", record_sidecar_reopen)
    try:
        enforce_private_sidecars(directory_fd, db_path)
    finally:
        os.close(directory_fd)

    assert reopened
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
