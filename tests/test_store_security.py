import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import Store, UnsafeDatabasePathError

if TYPE_CHECKING or os.name == "nt":
    from proactive_mcp.store import windows_path


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
    assert status.migration_version == 2


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
