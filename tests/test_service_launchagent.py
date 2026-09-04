from __future__ import annotations

import os
import stat
from pathlib import Path, PurePath, PurePosixPath
from typing import Final, Never

import pytest

from proactive_mcp.cli import service_launchagent
from proactive_mcp.cli.service_launchagent import (
    LAUNCHAGENT_FILENAME,
    LAUNCHAGENT_LABEL,
    PlistValue,
    UnmanagedArtifactError,
    UnsafeServiceArtifactError,
    UnsafeServiceValueError,
    delete_launch_agent_artifact,
    is_managed_launch_agent,
    parse_launch_agent_plist,
    read_launch_agent_artifact,
    render_launch_agent,
    write_launch_agent_artifact,
)

_EXECUTABLE: Final = PurePosixPath('/Applications/Proactive & MCP/bin/"proactive<mcp>"')
_DATABASE: Final = PurePosixPath(
    "/Users/test/Library/Application Support/proactive-mcp/state 'quoted' & <db>.sqlite"
)


class _InjectedWriteError(OSError):
    pass


def _plist_entries(content: bytes) -> dict[str, PlistValue]:
    parsed = parse_launch_agent_plist(content)
    assert isinstance(parsed, dict)
    return parsed


def _assert_mode(path: Path, expected: int) -> None:
    if os.name != "nt":
        assert stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) == expected


def test_rendered_plist_uses_literal_arguments_and_restart_policy() -> None:
    rendered = render_launch_agent(_EXECUTABLE, _DATABASE)
    entries = _plist_entries(rendered)
    arguments = entries["ProgramArguments"]
    environment = entries["EnvironmentVariables"]
    keep_alive = entries["KeepAlive"]

    assert entries["Label"] == LAUNCHAGENT_LABEL
    assert isinstance(arguments, list)
    assert arguments == [str(_EXECUTABLE), "daemon"]
    assert isinstance(environment, dict)
    assert environment["PROACTIVE_DATABASE"] == str(_DATABASE)
    assert environment["PROACTIVE_MCP_MANAGED"] == "1"
    assert entries["RunAtLoad"] is True
    assert isinstance(keep_alive, dict)
    assert keep_alive["SuccessfulExit"] is False
    assert entries["ThrottleInterval"] == 5
    assert entries["ProcessType"] == "Background"
    assert entries["Umask"] == 63
    assert "Program" not in entries
    assert b"&amp;" in rendered
    assert b"&lt;" in rendered


def test_managed_identity_requires_exact_label_and_marker() -> None:
    rendered = render_launch_agent(_EXECUTABLE, _DATABASE)
    wrong_label = rendered.replace(
        LAUNCHAGENT_LABEL.encode(),
        b"io.github.someone-else.proactive-mcp",
        1,
    )
    wrong_marker = rendered.replace(
        b"<string>1</string>",
        b"<string>0</string>",
        1,
    )

    assert is_managed_launch_agent(rendered) is True
    assert is_managed_launch_agent(wrong_label) is False
    assert is_managed_launch_agent(wrong_marker) is False
    assert is_managed_launch_agent(b"not a plist") is False


@pytest.mark.parametrize(
    ("executable", "database"),
    [
        (PurePosixPath("relative/proactive-mcp"), _DATABASE),
        (_EXECUTABLE, PurePosixPath("relative/proactive.db")),
    ],
)
def test_render_rejects_relative_paths(
    executable: PurePath,
    database: PurePath,
) -> None:
    with pytest.raises(UnsafeServiceValueError):
        _ = render_launch_agent(executable, database)


@pytest.mark.parametrize(
    "control",
    ["\0", "\n", "\r", "\x01", "\x1f", "\x7f", "\u202e"],
)
@pytest.mark.parametrize("target", ["executable", "database"])
def test_render_rejects_control_and_format_characters(
    control: str,
    target: str,
) -> None:
    executable = (
        PurePosixPath(f"/opt/proactive{control}/proactive-mcp")
        if target == "executable"
        else _EXECUTABLE
    )
    database = (
        PurePosixPath(f"/state/proactive{control}.db")
        if target == "database"
        else _DATABASE
    )

    with pytest.raises(UnsafeServiceValueError):
        _ = render_launch_agent(executable, database)


def test_managed_artifact_is_atomic_private_and_readable(tmp_path: Path) -> None:
    path = tmp_path / LAUNCHAGENT_FILENAME
    first = render_launch_agent(_EXECUTABLE, _DATABASE)
    second = render_launch_agent(_EXECUTABLE, PurePosixPath("/state/second.db"))

    write_launch_agent_artifact(path, first)
    _assert_mode(path, 0o600)
    assert read_launch_agent_artifact(path) == first

    write_launch_agent_artifact(path, second)
    _assert_mode(path, 0o600)
    assert read_launch_agent_artifact(path) == second


def test_artifact_write_restores_exact_mode_under_restrictive_umask(
    tmp_path: Path,
) -> None:
    path = tmp_path / LAUNCHAGENT_FILENAME
    managed = render_launch_agent(_EXECUTABLE, _DATABASE)
    old_umask = os.umask(0o077)
    try:
        write_launch_agent_artifact(path, managed, mode=0o640)
    finally:
        _ = os.umask(old_umask)

    _assert_mode(path, 0o640)


def test_failed_write_removes_private_temporary_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / LAUNCHAGENT_FILENAME
    managed = render_launch_agent(_EXECUTABLE, _DATABASE)

    def fail_write(_descriptor: int, _content: bytes) -> Never:
        raise _InjectedWriteError

    monkeypatch.setattr(service_launchagent, "_write_all", fail_write)

    with pytest.raises(_InjectedWriteError):
        write_launch_agent_artifact(path, managed)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["write", "delete"])
def test_artifact_mutation_revalidates_managed_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    path = tmp_path / LAUNCHAGENT_FILENAME
    managed = render_launch_agent(_EXECUTABLE, _DATABASE)
    write_launch_agent_artifact(path, managed)
    original_read = service_launchagent.read_launch_agent_artifact
    reads = 0
    foreign = b"foreign replacement"

    def race_target(candidate: Path) -> bytes | None:
        nonlocal reads
        reads += 1
        if reads == 2:
            _ = candidate.write_bytes(foreign)
        return original_read(candidate)

    monkeypatch.setattr(
        service_launchagent,
        "read_launch_agent_artifact",
        race_target,
    )

    def mutate() -> None:
        if operation == "write":
            write_launch_agent_artifact(path, managed)
        else:
            delete_launch_agent_artifact(path)

    with pytest.raises(UnmanagedArtifactError):
        mutate()

    assert path.read_bytes() == foreign


def test_unmanaged_artifact_is_never_changed_or_deleted(tmp_path: Path) -> None:
    path = tmp_path / LAUNCHAGENT_FILENAME
    original = b"foreign plist bytes"
    _ = path.write_bytes(original)
    path.chmod(0o644)
    managed = render_launch_agent(_EXECUTABLE, _DATABASE)

    with pytest.raises(UnmanagedArtifactError):
        write_launch_agent_artifact(path, managed)
    assert path.read_bytes() == original
    _assert_mode(path, 0o644)

    with pytest.raises(UnmanagedArtifactError):
        delete_launch_agent_artifact(path)
    assert path.read_bytes() == original
    _assert_mode(path, 0o644)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_artifact_operations_reject_link_aliases(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "target.plist"
    managed = render_launch_agent(_EXECUTABLE, _DATABASE)
    write_launch_agent_artifact(target, managed)
    candidate = tmp_path / LAUNCHAGENT_FILENAME
    if kind == "symlink":
        candidate.symlink_to(target)
    else:
        candidate.hardlink_to(target)

    with pytest.raises(UnsafeServiceArtifactError):
        _ = read_launch_agent_artifact(candidate if kind == "symlink" else target)
    with pytest.raises(UnsafeServiceArtifactError):
        write_launch_agent_artifact(
            candidate if kind == "symlink" else target,
            managed,
        )
    with pytest.raises(UnsafeServiceArtifactError):
        delete_launch_agent_artifact(candidate if kind == "symlink" else target)


def test_owned_delete_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / LAUNCHAGENT_FILENAME
    write_launch_agent_artifact(path, render_launch_agent(_EXECUTABLE, _DATABASE))

    delete_launch_agent_artifact(path)
    assert not path.exists()
    delete_launch_agent_artifact(path)
