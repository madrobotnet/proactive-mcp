from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import ClassVar, Literal
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

from proactive_mcp import cli
from proactive_mcp.cli import service as service_cli
from proactive_mcp.store import Store

PID = os.getpid()
UNIT_NAME = "proactive-mcp.service"
ENTRYPOINT = Path(__file__).parents[1] / ".venv" / "bin" / "proactive-mcp"


class ServiceResponse(BaseModel):
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
            "invalid_value",
            "io_failed",
        ]
        | None
    )


@dataclass(slots=True)
class FakeSystemdManager:
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
        return PID

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
class FakeDaemonStatus:
    liveness: Literal["running"] = "running"
    pid: int = PID


@dataclass(frozen=True, slots=True)
class FakeStatus:
    daemon: FakeDaemonStatus = FakeDaemonStatus()


@dataclass(frozen=True, slots=True)
class Harness:
    root: Path
    database: Path
    unit: Path
    state: Path
    env: dict[str, str]
    manager: FakeSystemdManager


def run_cli_in_process(
    harness: Harness,
    action: str,
) -> subprocess.CompletedProcess[str]:
    stdout = StringIO()
    stderr = StringIO()
    arguments = ["proactive-mcp", "service", action]

    def build_running_status() -> FakeStatus:
        return FakeStatus()

    with (
        patch.dict(os.environ, harness.env, clear=True),
        patch.object(sys, "platform", "linux"),
        patch.object(service_cli, "_MANAGER", harness.manager),
        patch.object(service_cli, "build_status", build_running_status),
        patch.object(sys, "argv", [str(ENTRYPOINT), "service", action]),
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


def run_cli(harness: Harness, action: str) -> subprocess.CompletedProcess[str]:
    if not sys.platform.startswith("linux"):
        return run_cli_in_process(harness, action)
    return subprocess.run(
        [ENTRYPOINT, "service", action],
        capture_output=True,
        text=True,
        env=harness.env,
        check=False,
        timeout=15,
    )


def make_harness(tmp_path: Path, *, fail_enable: bool = False) -> Harness:
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
  show) printf '%s\\n' "$SERVICE_FAKEPID" ;;
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
    unit = tmp_path / "xdg" / "systemd" / "user" / UNIT_NAME
    env = os.environ | {
        "PATH": f"{binary_dir}{os.pathsep}/usr/bin:/bin",
        "PROACTIVE_DATABASE": str(database),
        "SERVICE_FAKEPID": str(PID),
        "SERVICE_FAKE_STATE": str(state),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    if fail_enable:
        env["SERVICE_FAKE_FAIL_ENABLE"] = "1"
    manager = FakeSystemdManager(state, fail_enable=fail_enable)
    return Harness(tmp_path, database, unit, state, env, manager)


def record_running(database: Path) -> None:
    with Store(database) as store:
        store.daemon.record_start(PID)


def parse_response(result: subprocess.CompletedProcess[str]) -> ServiceResponse:
    return ServiceResponse.model_validate_json(result.stdout)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
