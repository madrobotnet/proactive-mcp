from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import final

import pytest

from proactive_mcp.cli import service_invocation
from proactive_mcp.cli.service_invocation import current_executable

_UNTRUSTED_KINDS = ["relative", "symlink"]
if os.name != "nt":
    _UNTRUSTED_KINDS.extend(("hardlink", "writable"))


def _executable(path: Path) -> Path:
    _ = path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


@dataclass(frozen=True, slots=True)
class _WindowsStat:
    st_mode: int
    st_uid: int
    st_nlink: int
    st_dev: int
    st_ino: int


@final
class _WindowsHardlinkOs:
    name = "nt"
    O_RDONLY = os.O_RDONLY
    O_PATH = getattr(os, "O_PATH", os.O_RDONLY)
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

    @staticmethod
    def open(path: Path, flags: int) -> int:
        return os.open(path, flags)

    @staticmethod
    def fstat(descriptor: int) -> _WindowsStat:
        observed = os.fstat(descriptor)
        return _WindowsStat(
            observed.st_mode,
            observed.st_uid,
            2,
            observed.st_dev,
            observed.st_ino,
        )

    @staticmethod
    def lstat(path: Path) -> _WindowsStat:
        observed = os.lstat(path)
        return _WindowsStat(
            observed.st_mode,
            observed.st_uid,
            2,
            observed.st_dev,
            observed.st_ino,
        )

    @staticmethod
    def close(descriptor: int) -> None:
        os.close(descriptor)


def test_current_absolute_invocation_is_used_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _executable(tmp_path / "proactive-mcp")
    monkeypatch.setattr(sys, "argv", [str(executable), "service", "install"])
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert current_executable() == executable


def test_windows_absolute_hardlinked_launcher_is_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _executable(tmp_path / "proactive-mcp.exe")
    monkeypatch.setattr(sys, "argv", [str(executable), "service", "install"])
    monkeypatch.setattr(service_invocation, "os", _WindowsHardlinkOs())

    assert current_executable() == executable


@pytest.mark.parametrize("kind", _UNTRUSTED_KINDS)
def test_ambiguous_or_untrusted_invocation_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    executable = _executable(tmp_path / "proactive-mcp")
    candidate = executable
    if kind == "relative":
        candidate = Path(executable.name)
    elif kind == "symlink":
        candidate = tmp_path / "proactive-link"
        candidate.symlink_to(executable)
    elif kind == "hardlink":
        os.link(executable, tmp_path / "proactive-alias")
    else:
        executable.chmod(0o722)
    monkeypatch.setattr(sys, "argv", [str(candidate), "service", "install"])

    assert current_executable() is None
