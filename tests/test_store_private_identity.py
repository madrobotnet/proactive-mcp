from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store, UnsafeDatabasePathError
from proactive_mcp.store.private_path import enforce_private_sidecars

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX identity contract")


def _hardlink(path: Path) -> Path:
    alias = path.with_name(f"{path.name}.alias")
    os.link(path, alias)
    return alias


def test_store_rejects_hardlinked_database(tmp_path: Path) -> None:
    database = tmp_path / "proactive.db"
    with Store(database):
        pass
    alias = _hardlink(database)

    with pytest.raises(UnsafeDatabasePathError), Store(database):
        pass

    assert database.stat().st_ino == alias.stat().st_ino


def test_store_rejects_hardlinked_initialization_lock(tmp_path: Path) -> None:
    database = tmp_path / "proactive.db"
    with Store(database):
        pass
    lock = tmp_path / ".proactive.db.init.lock"
    _ = _hardlink(lock)

    with pytest.raises(UnsafeDatabasePathError), Store(database):
        pass


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecar_enforcement_rejects_hardlinks(tmp_path: Path, suffix: str) -> None:
    database = tmp_path / "proactive.db"
    database.touch(mode=0o600)
    sidecar = database.with_name(f"{database.name}{suffix}")
    sidecar.touch(mode=0o600)
    _ = _hardlink(sidecar)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(UnsafeDatabasePathError):
            enforce_private_sidecars(directory_fd, database)
    finally:
        os.close(directory_fd)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux deterministic open race")
def test_database_link_race_is_revalidated_before_store_is_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "proactive.db"
    original_open = os.open
    raced = False

    def hardlink_after_database_open(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == database.name and flags & os.O_RDWR and not raced:
            raced = True
            os.link(
                database.name,
                f"{database.name}.alias",
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
        return descriptor

    monkeypatch.setattr(os, "open", hardlink_after_database_open)

    with pytest.raises(UnsafeDatabasePathError), Store(database):
        pass
