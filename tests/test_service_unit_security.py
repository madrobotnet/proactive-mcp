from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from proactive_mcp.cli.service_unit import UnsafeServiceValueError, render_user_unit


def _directives(unit: str, name: str) -> list[str]:
    prefix = f"{name}="
    return [line for line in unit.splitlines() if line.startswith(prefix)]


def _decode_quoted(value: str) -> str:
    assert value.startswith('"')
    assert value.endswith('"')
    body = value[1:-1]
    output: list[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            index += 1
            character = body[index]
        elif character == "%":
            assert body[index : index + 2] == "%%"
            index += 1
        output.append(character)
        index += 1
    return "".join(output)


@pytest.mark.parametrize(
    ("executable", "database"),
    [
        (
            Path('/opt/Proactive MCP/bin/proactive%"\\mcp'),
            Path('/home/user/state/proactive %"\\.db'),
        ),
        (
            PureWindowsPath(r"C:\Program Files\Proactive%\proactive-mcp.exe"),
            PureWindowsPath(r"C:\Users\Name\State %\proactive.db"),
        ),
    ],
)
def test_rendered_values_remain_single_systemd_assignments(
    executable: Path | PureWindowsPath,
    database: Path | PureWindowsPath,
) -> None:
    unit = render_user_unit(executable, database)

    exec_start = _directives(unit, "ExecStart")
    environment = _directives(unit, "Environment")
    assert len(exec_start) == 1
    assert not _directives(unit, "ExecStartPre")
    assert not _directives(unit, "ExecStartPost")
    assert len(environment) == 1
    assert _decode_quoted(environment[0].removeprefix("Environment=")) == (
        f"PROACTIVE_DATABASE={database!s}"
    )
    command = exec_start[0].removeprefix("ExecStart=").removesuffix(" daemon")
    assert _decode_quoted(command) == str(executable)


@pytest.mark.parametrize(
    "control",
    ["\0", "\n", "\r", "\x01", "\x1f", "\x7f", "\u202e"],
)
@pytest.mark.parametrize("target", ["executable", "database"])
def test_render_rejects_systemd_control_characters(control: str, target: str) -> None:
    executable = Path(f"/opt/proactive{control}/proactive-mcp")
    database = Path(f"/home/user/state{control}/proactive.db")
    if target == "executable":
        database = Path("/home/user/state/proactive.db")
    else:
        executable = Path("/opt/proactive/proactive-mcp")

    with pytest.raises(UnsafeServiceValueError):
        _ = render_user_unit(executable, database)
