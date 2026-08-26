from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import UnsafeDatabasePathError
from proactive_mcp.store.private_file import (
    delete_private_file,
    read_private_text,
    write_private_text,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX identity contract")


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_private_file_operations_reject_hardlinked_destination(
    tmp_path: Path,
    operation: str,
) -> None:
    path = tmp_path / "credentials.json"
    _ = path.write_text("secret", encoding="utf-8")
    path.chmod(0o600)
    os.link(path, tmp_path / "credentials.alias")

    def perform() -> None:
        if operation == "read":
            _ = read_private_text(path)
        elif operation == "write":
            write_private_text(path, "replacement")
        else:
            delete_private_file(path)

    with pytest.raises(UnsafeDatabasePathError):
        perform()


@pytest.mark.parametrize("name", ["credentials.json", "credentials.state.json"])
def test_credential_primary_and_state_files_reject_hardlinks(
    tmp_path: Path,
    name: str,
) -> None:
    path = tmp_path / name
    write_private_text(path, "private")
    os.link(path, tmp_path / f"{name}.alias")

    with pytest.raises(UnsafeDatabasePathError):
        _ = read_private_text(path)


def test_private_temporary_link_race_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "credentials.json"
    original_fsync = os.fsync
    raced = False

    def hardlink_before_replace(descriptor: int) -> None:
        nonlocal raced
        if not raced and stat.S_ISREG(os.fstat(descriptor).st_mode):
            targets = tuple(tmp_path.glob(".*.tmp"))
            if targets:
                raced = True
                os.link(targets[0], tmp_path / "temporary.alias")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", hardlink_before_replace)

    with pytest.raises(UnsafeDatabasePathError):
        write_private_text(path, "private")

    assert not path.exists()


def test_private_read_link_race_is_revalidated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "credentials.json"
    write_private_text(path, "private")
    original_fstat = os.fstat
    checks = 0

    def hardlink_before_final_check(descriptor: int) -> os.stat_result:
        nonlocal checks
        observed = original_fstat(descriptor)
        if stat.S_ISREG(observed.st_mode):
            checks += 1
            if checks == 3:
                os.link(path, tmp_path / "credentials.alias")
                observed = original_fstat(descriptor)
        return observed

    monkeypatch.setattr(os, "fstat", hardlink_before_final_check)

    with pytest.raises(UnsafeDatabasePathError):
        _ = read_private_text(path)
