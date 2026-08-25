from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

from proactive_mcp import cli
from proactive_mcp.cli import service as service_cli
from proactive_mcp.cli.service_unit import render_user_unit
from proactive_mcp.store import Store

if TYPE_CHECKING:
    import pytest

_PID = os.getpid()
_UNIT_NAME = "proactive-mcp.service"


class _ServiceResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    action: Literal["install", "status", "remove"]
    state: Literal[
        "installed",
        "active",
        "inactive",
        "removed",
        "absent",
        "unsupported",
        "failed",
        "unmanaged",
    ]
    unit: str
    managed: bool
    enabled: bool
    active: bool
    main_pid: int | None
    heartbeat: Literal["running", "stopped", "stale", "never_started"] | None
    linger: Literal["enabled", "disabled", "unknown", "not_applicable"]
    guidance: Literal["none", "enable_linger"]
    code: (
        Literal[
            "unsupported_platform",
            "binary_not_found",
            "unmanaged_unit",
            "command_failed",
            "heartbeat_unavailable",
            "io_failed",
        ]
        | None
    )


@dataclass(slots=True)
class _FakeSystemdManager:
    state: Path
    fail_enable: bool = False
    enabled: bool = False
    active: bool = False

    def reload(self) -> bool:
        return True

    def enable(self) -> bool:
        if self.fail_enable:
            return False
        self.enabled = True
        self._sync()
        return True

    def start(self) -> bool:
        self.active = True
        self._sync()
        return True

    def stop(self) -> bool:
        self.active = False
        self._sync()
        return True

    def disable(self) -> bool:
        self.enabled = False
        self._sync()
        return True

    def is_enabled(self) -> bool:
        return self.enabled

    def is_active(self) -> bool:
        return self.active

    def main_pid(self) -> int:
        return _PID

    def linger(self) -> Literal["enabled"]:
        return "enabled"

    def _sync(self) -> None:
        values = [
            value
            for value, selected in (
                ("enabled", self.enabled),
                ("active", self.active),
            )
            if selected
        ]
        if values:
            _ = self.state.write_text("\n".join(values) + "\n", encoding="utf-8")
        else:
            self.state.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class _FakeDaemonStatus:
    liveness: Literal["running"] = "running"
    pid: int = _PID


@dataclass(frozen=True, slots=True)
class _FakeStatus:
    daemon: _FakeDaemonStatus = _FakeDaemonStatus()


@dataclass(frozen=True, slots=True)
class _Harness:
    root: Path
    database: Path
    unit: Path
    state: Path
    env: dict[str, str]
    manager: _FakeSystemdManager


def _run_cli_in_process(
    harness: _Harness,
    action: str,
) -> subprocess.CompletedProcess[str]:
    stdout = StringIO()
    stderr = StringIO()
    arguments = ["proactive-mcp", "service", action]

    def build_running_status() -> _FakeStatus:
        return _FakeStatus()

    def find_executable(_name: str) -> str:
        return sys.executable

    with (
        patch.dict(os.environ, harness.env, clear=True),
        patch.object(sys, "platform", "linux"),
        patch.object(service_cli, "_MANAGER", harness.manager),
        patch.object(service_cli, "build_status", build_running_status),
        patch.object(shutil, "which", find_executable),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        returncode = cli.main(["service", action])
    return subprocess.CompletedProcess(
        arguments,
        returncode,
        stdout.getvalue(),
        stderr.getvalue(),
    )


def _run_cli(harness: _Harness, action: str) -> subprocess.CompletedProcess[str]:
    if not sys.platform.startswith("linux"):
        return _run_cli_in_process(harness, action)
    command = (
        "from proactive_mcp.cli import entrypoint; "
        "import sys; "
        "sys.platform = 'linux'; "
        "entrypoint()"
    )
    return subprocess.run(
        [sys.executable, "-c", command, "service", action],
        capture_output=True,
        text=True,
        env=harness.env,
        check=False,
        timeout=15,
    )


def _make_harness(tmp_path: Path, *, fail_enable: bool = False) -> _Harness:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    state = tmp_path / "systemctl.state"
    systemctl = binary_dir / "systemctl"
    _ = systemctl.write_text(
        """#!/bin/sh
set -eu
[ "$1" = "--user" ] && shift
case "$1" in
  daemon-reload) exit 0 ;;
  enable)
    [ "${SERVICE_FAKE_FAIL_ENABLE:-0}" = "0" ] || exit 1
    touch "$SERVICE_FAKE_STATE"
    if ! grep -q enabled "$SERVICE_FAKE_STATE"; then
      printf 'enabled\\n' >> "$SERVICE_FAKE_STATE"
    fi
    ;;
  start)
    touch "$SERVICE_FAKE_STATE"
    grep -q active "$SERVICE_FAKE_STATE" || printf 'active\\n' >> "$SERVICE_FAKE_STATE"
    ;;
  stop)
    [ ! -f "$SERVICE_FAKE_STATE" ] || sed -i '/active/d' "$SERVICE_FAKE_STATE"
    ;;
  disable)
    if [ -f "$SERVICE_FAKE_STATE" ]; then
      sed -i '/enabled/d' "$SERVICE_FAKE_STATE"
      [ -s "$SERVICE_FAKE_STATE" ] || rm "$SERVICE_FAKE_STATE"
    fi
    ;;
  is-enabled)
    [ -f "$SERVICE_FAKE_STATE" ] && grep -q enabled "$SERVICE_FAKE_STATE" && {
      printf 'enabled\\n'; exit 0;
    }
    printf 'disabled\\n'; exit 1
    ;;
  is-active)
    [ -f "$SERVICE_FAKE_STATE" ] && grep -q active "$SERVICE_FAKE_STATE" && {
      printf 'active\\n'; exit 0;
    }
    printf 'inactive\\n'; exit 3
    ;;
  show) printf '%s\\n' "$SERVICE_FAKE_PID" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    loginctl = binary_dir / "loginctl"
    _ = loginctl.write_text("#!/bin/sh\nprintf 'yes\\n'\n", encoding="utf-8")
    loginctl.chmod(0o755)
    database = tmp_path / "state" / "proactive.db"
    unit = tmp_path / "xdg" / "systemd" / "user" / _UNIT_NAME
    env = os.environ | {
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "PROACTIVE_DATABASE": str(database),
        "SERVICE_FAKE_PID": str(_PID),
        "SERVICE_FAKE_STATE": str(state),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    if fail_enable:
        env["SERVICE_FAKE_FAIL_ENABLE"] = "1"
    manager = _FakeSystemdManager(state, fail_enable=fail_enable)
    return _Harness(tmp_path, database, unit, state, env, manager)


def _record_running(database: Path) -> None:
    with Store(database) as store:
        store.daemon.record_start(_PID)


def _response(result: subprocess.CompletedProcess[str]) -> _ServiceResponse:
    return _ServiceResponse.model_validate_json(result.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rendered_unit_restarts_only_retryable_daemon_failures() -> None:
    # Given: an isolated executable and profile database.
    executable = Path("/opt/proactive/bin/proactive-mcp")
    database = Path("/var/lib/proactive/proactive.db")

    # When: the managed user unit is rendered.
    directives = render_user_unit(executable, database).splitlines()

    # Then: systemd retries failures except the permanent exit status.
    assert "Restart=on-failure" in directives
    assert "RestartPreventExitStatus=2" in directives


def test_service_help_exposes_install_status_and_remove() -> None:
    # Given: the installed CLI entry point.

    # When: a user asks for service lifecycle help.
    result = subprocess.run(
        [sys.executable, "-m", "proactive_mcp", "service", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    # Then: all supported lifecycle actions are discoverable.
    assert result.returncode == 0
    assert "install" in result.stdout
    assert "status" in result.stdout
    assert "remove" in result.stdout


def test_portable_in_process_harness_exercises_linux_contract(tmp_path: Path) -> None:
    # Given: the cross-platform harness selects the Linux service contract.
    harness = _make_harness(tmp_path)
    _record_running(harness.database)

    # When: installation uses the in-process manager used by Windows CI.
    result = _run_cli_in_process(harness, "install")

    # Then: it preserves the same managed lifecycle response as POSIX CI.
    response = _response(result)
    assert result.returncode == 0
    assert response.state == "installed"
    assert response.enabled is True
    assert response.active is True
    assert response.main_pid == _PID


def test_install_writes_absolute_managed_restartable_unit(tmp_path: Path) -> None:
    # Given: a fake user systemd manager and a current daemon heartbeat.
    harness = _make_harness(tmp_path)
    _record_running(harness.database)

    # When: the service is installed through the real CLI process.
    result = _run_cli(harness, "install")

    # Then: systemd reports a managed, enabled, active, heartbeat-backed unit.
    response = _response(result)
    unit = harness.unit.read_text(encoding="utf-8")
    exec_start = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert result.returncode == 0
    assert response.state == "installed"
    assert response.managed is True
    assert response.enabled is True
    assert response.active is True
    assert response.main_pid == _PID
    assert response.heartbeat == "running"
    assert response.linger == "enabled"
    assert response.guidance == "none"
    rendered_executable = (
        exec_start.removeprefix("ExecStart=")
        .removesuffix(" daemon")
        .strip('"')
        .replace("\\\\", "\\")
    )
    assert Path(rendered_executable).is_absolute()
    assert " daemon" in exec_start
    assert "Restart=on-failure" in unit.splitlines()
    assert "RestartPreventExitStatus=2" in unit.splitlines()
    assert "Type=notify" in unit.splitlines()
    assert "PrivateTmp=true" not in unit.splitlines()
    assert "X-Proactive-MCP-Managed=true" in unit.splitlines()


def test_install_is_idempotent(tmp_path: Path) -> None:
    # Given: an already installed managed service.
    harness = _make_harness(tmp_path)
    _record_running(harness.database)
    assert _run_cli(harness, "install").returncode == 0
    before = harness.unit.read_bytes()

    # When: install is repeated.
    repeated = _run_cli(harness, "install")

    # Then: the same unit remains active without changing its content.
    assert repeated.returncode == 0
    assert _response(repeated).state == "installed"
    assert harness.unit.read_bytes() == before


def test_status_reports_active_service_and_heartbeat(tmp_path: Path) -> None:
    # Given: an installed service with a matching active process and heartbeat.
    harness = _make_harness(tmp_path)
    _record_running(harness.database)
    assert _run_cli(harness, "install").returncode == 0

    # When: status is queried through the CLI.
    result = _run_cli(harness, "status")

    # Then: service-manager and persisted liveness agree.
    response = _response(result)
    assert result.returncode == 0
    assert response.state == "active"
    assert response.main_pid == _PID
    assert response.heartbeat == "running"


def test_remove_preserves_database_config_oauth_and_other_profiles(
    tmp_path: Path,
) -> None:
    # Given: an installed unit and profile state outside the unit path.
    harness = _make_harness(tmp_path)
    _record_running(harness.database)
    config = harness.database.parent / "config.toml"
    oauth = harness.database.parent / "credentials" / "google-readonly-oauth.json"
    tombstone = oauth.with_name("google-readonly-oauth.state.json")
    unrelated = harness.root / "other-profile.json"
    oauth.parent.mkdir()
    _ = config.write_text("[daemon]\n", encoding="utf-8")
    _ = oauth.write_text("oauth-canary", encoding="utf-8")
    _ = tombstone.write_text("tombstone-canary", encoding="utf-8")
    _ = unrelated.write_text("other-profile-canary", encoding="utf-8")
    assert _run_cli(harness, "install").returncode == 0
    preserved_paths = (harness.database, config, oauth, tombstone, unrelated)
    preserved = {path: _sha256(path) for path in preserved_paths}

    # When: the managed service is removed.
    result = _run_cli(harness, "remove")

    # Then: only the unit is gone and all profile state is byte-identical.
    assert result.returncode == 0
    assert _response(result).state == "removed"
    assert not harness.unit.exists()
    assert not harness.state.exists()
    assert {path: _sha256(path) for path in preserved} == preserved


def test_remove_is_idempotent_when_unit_is_absent(tmp_path: Path) -> None:
    # Given: one remove already observed that no managed service exists.
    harness = _make_harness(tmp_path)
    first = _run_cli(harness, "remove")
    assert first.returncode == 0

    # When: remove is requested again.
    second = _run_cli(harness, "remove")

    # Then: the repeated call reports the same harmless absent state.
    assert second.returncode == 0
    assert _response(first).state == "absent"
    assert _response(second).state == "absent"


def test_install_rolls_back_new_unit_when_enable_fails(tmp_path: Path) -> None:
    # Given: a user manager that rejects enable/start.
    harness = _make_harness(tmp_path, fail_enable=True)

    # When: installation reaches the failing systemctl operation.
    result = _run_cli(harness, "install")

    # Then: the typed failure leaves no unit or enabled state behind.
    response = _response(result)
    assert result.returncode == 2
    assert response.state == "failed"
    assert response.code == "command_failed"
    assert not harness.unit.exists()
    assert not harness.state.exists()


def test_install_refuses_to_overwrite_unmanaged_unit(tmp_path: Path) -> None:
    # Given: an existing same-name unit without the ownership marker.
    harness = _make_harness(tmp_path)
    harness.unit.parent.mkdir(parents=True)
    _ = harness.unit.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
    before = harness.unit.read_bytes()

    # When: install is requested.
    result = _run_cli(harness, "install")

    # Then: the foreign unit is unchanged and no service command ran.
    response = _response(result)
    assert result.returncode == 2
    assert response.state == "unmanaged"
    assert response.code == "unmanaged_unit"
    assert harness.unit.read_bytes() == before
    assert not harness.state.exists()


def test_non_linux_returns_typed_unsupported_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the CLI boundary is running on a non-Linux platform.
    monkeypatch.setattr(sys, "platform", "darwin")

    # When: service installation is requested.
    result = cli.main(["service", "install"])
    captured = capsys.readouterr()

    # Then: a closed machine-readable unsupported result is returned.
    response = _ServiceResponse.model_validate_json(captured.out)
    assert result == 2
    assert response.state == "unsupported"
    assert response.code == "unsupported_platform"
    assert response.linger == "not_applicable"
