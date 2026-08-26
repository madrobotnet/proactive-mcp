from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from proactive_mcp.cli.service_invocation import current_executable

_UNTRUSTED_KINDS = ["relative", "symlink", "hardlink"]
if os.name != "nt":
    _UNTRUSTED_KINDS.append("writable")


def _executable(path: Path) -> Path:
    _ = path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_current_absolute_invocation_is_used_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _executable(tmp_path / "proactive-mcp")
    monkeypatch.setattr(sys, "argv", [str(executable), "service", "install"])
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

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
